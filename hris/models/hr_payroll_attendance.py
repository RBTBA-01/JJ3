# -*- coding:utf-8 -*-
from odoo import api, fields, models, tools
from odoo.exceptions import ValidationError, UserError
from odoo.tools.translate import _
from datetime import timedelta, datetime, date
from odoo.tools import float_round, DEFAULT_SERVER_DATETIME_FORMAT
from dateutil import rrule
import time
import math
import pytz
import babel

from lxml import etree
from odoo.fields import Datetime
from collections import OrderedDict
from odoo.osv.orm import setup_modifiers
from dateutil.relativedelta import relativedelta

import sys
reload(sys)
sys.setdefaultencoding('utf8')
import logging
logger = logging.getLogger(__name__)


# redefined imports
context_timestamp = Datetime.context_timestamp
from_string = Datetime.from_string
to_string = Datetime.to_string

TIME_TO_RENDER = 8.0
HOURS_PER_DAY = 8.0


def convert_time_to_float(time):
    hours = time.seconds // 3600
    minutes = (time.seconds // 60) % 60
    total_hrs = float(minutes) / 60 + hours
    return total_hrs


def float_time_convert(float_value, is_midnight=False):
    """Splits float value into hour and minute."""

    minute, hour = math.modf(float_value)
    minute = minute * 60
    hour = int(round(hour, 2))
    minute = int(round(minute, 2))

    return hour, minute


def context_utc(date, timezone):
    """Returns date and time into utc format."""
    if not date:
        return ''

    if not timezone:
        timezone = 'UTC'

    tz = pytz.timezone(timezone)
    local_date = tz.localize(date, is_dst=None)
    utc_date = local_date.astimezone(pytz.utc)

    return utc_date


def get_intersection(date_in, date_out, required_in, to_render):
    """Returns the required in if intersected with the schedule."""
    if date_in.hour < 6 and date_out < required_in:
        required_in_yesterday = required_in - timedelta(days=1)

        required_out_hour, required_out_minute = float_time_convert(to_render)
        required_out_yesterday = required_in_yesterday +\
            timedelta(hours=required_out_hour, minutes=required_out_minute, seconds=0)

        if required_in_yesterday <= date_in <= required_out_yesterday:
            required_in = required_in_yesterday

        return required_in
    else:
        return required_in


class HRAttendance(models.Model):
    _inherit = 'hr.attendance'

    check_in = fields.Datetime(string="Check In", default=False, required=False)

    @api.multi
    def name_get(self):
        result = []
        for attendance in self:
            if not attendance.check_in and not attendance.check_out and attendance.schedule_in:
                result.append((attendance.id, _("%(empl_name)s from %(schedule_in)s") % {
                    'empl_name': attendance.employee_id.name_related,
                    'schedule_in': fields.Datetime.to_string(fields.Datetime.context_timestamp(
                        attendance, fields.Datetime.from_string(attendance.schedule_in))),
                }))
            elif not attendance.check_out:
                result.append((attendance.id, _("%(empl_name)s from %(check_in)s") % {
                    'empl_name': attendance.employee_id.name_related,
                    'check_in': fields.Datetime.to_string(fields.Datetime.context_timestamp(
                        attendance, fields.Datetime.from_string(attendance.check_in))),
                }))
            else:
                result.append((attendance.id, _("%(empl_name)s from %(check_in)s to %(check_out)s") % {
                    'empl_name': attendance.employee_id.name_related,
                    'check_in': fields.Datetime.to_string(fields.Datetime.context_timestamp(
                        attendance, fields.Datetime.from_string(attendance.check_in))),
                    'check_out': fields.Datetime.to_string(fields.Datetime.context_timestamp(
                        attendance, fields.Datetime.from_string(attendance.check_out))),
                }))
        return result

    def get_work_time_lines(self, domain):
        """Assigns work time line automatically based on employee check-in """
        if not self.check_in:
            return
        work_time = self.env['hr.employee.schedule.work_time']. \
            search(domain, order="priority desc", limit=1)
        day_name = context_timestamp(self, from_string(self.check_in)).strftime('%A').lower()
        work_time_line = work_time.work_time_lines.filtered(lambda r: r.days_of_week == day_name)

        # Get the list of present and previous work timeline to determine the
        # intersection between previous worked day end time and present check in time for
        # night shift diff.

        actual_time_in = context_timestamp(self, from_string(self.check_in))
        actual_time_in_hour = actual_time_in.hour
        if actual_time_in_hour <= 6:
            timeline_list = []

            def get_intersection(days=0):
                check_in = actual_time_in - timedelta(days=days)
                day_name = check_in.strftime('%A').lower()

                work_time_line = work_time.work_time_lines.filtered(lambda r: r.days_of_week == day_name)

                for line in work_time_line:
                    required_in_hour, required_in_minute = float_time_convert(line.latest_check_in)
                    required_out_hour, required_out_minute = float_time_convert(line.time_to_render)

                    required_in = check_in.replace(hour=required_in_hour, minute=required_in_minute, second=0)
                    required_out = required_in + timedelta(hours=required_out_hour, minutes=required_out_minute,
                                                           seconds=0)

                    timeline_list.append((required_in, required_out))

                if days < 2:
                    days += 1
                    get_intersection(days)

            get_intersection(0)

            for timeline in timeline_list:
                start_time, end_time = timeline

                if start_time <= actual_time_in <= end_time:
                    day_name = start_time.strftime('%A').lower()
                    work_time_line = work_time.work_time_lines.filtered(lambda r: r.days_of_week == day_name)

        res = []

        for line in work_time_line:
            date_in = context_timestamp(self, from_string(self.check_in))
            hour, minute = float_time_convert(line.latest_check_in)

            diff = (date_in - date_in.replace(hour=hour, minute=minute, second=0)).total_seconds() / 3600.0
            res.append((abs(diff), line))

        line_id = res and min(res, key=lambda r: r[0]) or False

        self.work_time_line_id = line_id and line_id[1].id or False

    def create_attendance(self, employee, start_date, end_date):
        """Creates an attendance record of an employee.
        Absent: Employee without time in.
        Leaves: Employee without attendance but there's an approved leaves.
        Holiday: Employee without attendance but it is holiday.
        """
        values = {
            'employee_id': employee.id,
            'check_in': start_date,
            'check_out': end_date,
        }
        attendance = self.env['hr.attendance'].search([(
            'employee_id', '=', employee.id),
            ('check_in', '<=', start_date),
            ('check_out', '>=', end_date)
        ], limit=1)

        attendance |= self.env['hr.attendance'].search([
            ('employee_id', '=', employee.id),
            ('check_in', '>=', start_date),
            ('check_in', '<=', end_date)
        ], limit=1)

        attendance |= self.env['hr.attendance'].search([
            ('employee_id', '=', employee.id),
            ('check_in', '<=', end_date),
            ('check_out', '>=', start_date)
        ], limit=1)

        leaves = self.env['hr.holidays'].search([
            ('state', '=', 'validate'),
            ('employee_id', '=', employee.id),
            ('type', '=', 'remove'),
            ('process_type', '=', False),
            ('date_from', '<=', start_date),
            ('date_to', '>=', end_date)
        ])

        if leaves:

            values['is_leave'] = True
            values['remarks'] = leaves[0].holiday_status_id.name

        domain = [('holiday_start', '<=', start_date),
                  ('holiday_end', '>=', start_date)]

        holidays = self.env['hr.attendance.holidays'].search(domain)

        if holidays:
            holiday_type = dict(self.env['hr.attendance.holidays'].
                                fields_get(allfields=['holiday_type'])['holiday_type']['selection'])[
                holidays[0].holiday_type]
            values['is_holiday'] = True
            values['remarks'] = holiday_type

        if not (leaves or holidays):
            values['is_absent'] = True
            values['remarks'] = 'ABS'

        if attendance:
            attendance.write(values)
            return True

        return self.create(values)

    def get_employees(self):
        """Return all unresigned and active employees."""
        contracts = self.env['hr.contract'].search([('state', '=', 'open'), ('resigned', '=', False)])
        employee_ids = contracts.mapped('employee_id')
        return employee_ids

    def get_employee_worked_schedule(self, employee, department_id, date_today, day_name):
        """Returns employee todays work schedule."""

        domain = [('employee_id', '=', employee.id)]

        if department_id:
            domain = [
                ('department_id', '=', department_id.id)
            ]

        domain += [
            ('start_date', '<=', date_today), ('end_date', '>=', date_today),
            ('state', '=', 'approved')
        ]

        work_time = self.env['hr.employee.schedule.work_time']. \
            search(domain, order='priority desc', limit=1)

        work_time_line = work_time.work_time_lines.filtered(lambda r: r.days_of_week == day_name)

        return work_time_line

    def get_employee_attendance(self, employee, date_today=None):
        """Returns employee todays attendances."""

        start_date = date_today.replace(hour=0, minute=0, second=0)
        end_date = date_today.replace(hour=23, minute=59, second=59)

        timezone = self._context.get('tz') or self.env.user.tz

        date_from = to_string(context_utc(from_string(to_string(start_date)), timezone))
        date_to = to_string(context_utc(from_string(to_string(end_date)), timezone))

        domain = [
            ('employee_id', '=', employee.id),
            ('check_in', '>=', date_from),
            ('check_in', '<=', date_to)
        ]
        attendances = self.search(domain)

        return attendances

    def make_absent(self, date_from=None, date_to=None, emp_ids=[]):
        """This is a daily cron job at end of the day, probably 11:59PM."""

        day_from = from_string(date_from)
        day_to = from_string(date_to)
        nb_of_days = (day_to - day_from).days

        # Gather all intervals and holidays

        employees = emp_ids or self.get_employees()
        for day in range(0, nb_of_days):

            date_today = day_from + timedelta(days=day)

            today = context_timestamp(self, date_today)
            date_utc_today = to_string(date_today)

            day_name = today.strftime('%A').lower()
            # insert here filtering based on date hired

            for employee in employees:
                # Prioritize by employee schedule

                work_schedule_by_employee = self.get_employee_worked_schedule(employee, False, date_utc_today, day_name)
                if work_schedule_by_employee:
                    attendances = self.get_employee_attendance(employee, today)

                    absent_worked_schedule = work_schedule_by_employee - attendances.mapped('work_time_line_id')
                    for timeline in absent_worked_schedule:

                        self.create_absent_work_schedule(employee, timeline, today)

                # If no work schedule by employee found
                if not work_schedule_by_employee:
                    work_schedule_by_department = self.get_employee_worked_schedule(employee, employee.department_id,
                                                                                    date_utc_today, day_name)
                    attendances = self.get_employee_attendance(employee, today)

                    absent_worked_schedule = work_schedule_by_department - attendances.mapped('work_time_line_id')
                    for timeline in absent_worked_schedule:
                        self.create_absent_work_schedule(employee, timeline, today)

        return True

    def create_absent_work_schedule(self, employee, timeline, today):
        """Create employees absent work schedule.
           Converts from local to utc datetime schedule
        """
        required_in_hour, required_in_minute = float_time_convert(timeline.latest_check_in)
        if timeline and timeline.work_time_id.schedule_type == 'coretime':
            required_in_hour, required_in_minute = float_time_convert(timeline.earliest_check_in)

        required_out_hour, required_out_minute = float_time_convert(timeline.time_to_render)

        required_in = today.replace(hour=required_in_hour, minute=required_in_minute, second=0)
        required_out = required_in + timedelta(hours=required_out_hour, minutes=required_out_minute, seconds=0)

        parsed_required_in = from_string(to_string(required_in))
        parsed_required_out = from_string(to_string(required_out))

        utc_dt_from = context_utc(parsed_required_in, self._context.get('tz'))
        utc_dt_to = context_utc(parsed_required_out, self._context.get('tz'))

        time_in = to_string(utc_dt_from)
        time_out = to_string(utc_dt_to)

        res = self.create_attendance(employee, time_in, time_out)

        return res

    @api.multi
    def get_overtime_reference(self, rest_day=False):
        """Sets overtime reference, if overtime date is not on
        the work time schedule it is rest day."""
        if not self.check_in or not self.check_out:
            return False

        ob_leaves = self.leave_ids.filtered(lambda l: l.holiday_status_id.is_ob)
        date_in = from_string(self.check_in).strftime('%Y-%m-%d %H:%M')
        date_out = from_string(self.check_out).strftime('%Y-%m-%d %H:%M')

        domain = [('employee_id', '=', self.employee_id.id),
                  ('start_time', '<=', date_out),
                  ('start_time', '>=', date_in),
                  ('state', '=', 'approved')]

        overtime = self.env['hr.attendance.overtime'].search(domain, limit=1)
        if self.request_change_id and self.request_change_id.state == 'approved':
            if self.temp_in and self.temp_out:
                date_in = from_string(self.check_in).strftime('%Y-%m-%d %H:%M')
                date_out = from_string(self.check_out).strftime('%Y-%m-%d %H:%M')

                domain = [('employee_id', '=', self.employee_id.id),
                          ('start_time', '<=', date_out),
                          ('start_time', '>=', date_in),
                          ('state', '=', 'approved')]

                overtime = self.env['hr.attendance.overtime'].search(domain, limit=1)
        # Based on work schedule
        if not overtime and self.work_time_line_id:
            date_in = from_string(self.schedule_in).strftime('%Y-%m-%d %H:%M')
            date_out = from_string(self.schedule_out).strftime('%Y-%m-%d %H:%M')

            domain = [('employee_id', '=', self.employee_id.id),
                      ('start_time', '<=', date_out),
                      ('start_time', '>=', date_in),
                      ('state', '=', 'approved')]

            overtime = self.env['hr.attendance.overtime'].search(domain, limit=1)
        # Based On OB
        if not overtime and ob_leaves:
            date_in = from_string(min(ob_leaves.mapped('date_from'))).strftime('%Y-%m-%d %H:%M')
            date_out = from_string(max(ob_leaves.mapped('date_to'))).strftime('%Y-%m-%d %H:%M')

            domain = [('employee_id', '=', self.employee_id.id),
                      ('start_time', '<=', date_out),
                      ('start_time', '>=', date_in),
                      ('state', '=', 'approved')]

            overtime = self.env['hr.attendance.overtime'].search(domain, limit=1)

        if rest_day:
            check_in = fields.Datetime.from_string(self.check_in)
            check_out = fields.Datetime.from_string(self.check_out)
            duration = (check_out - check_in).total_seconds() / 3600.0
            if duration > 8:
                # If the duration is exceeds 8 hours, set rest_day_hours to 8 hours
                self.rest_day_hours = 8
            else:
                # If the duration is 8 hours or less, set rest_day_hours to the actual duration
                self.rest_day_hours = duration

            self.remarks = 'RD'
            # Only set the rest day overtime if it's rest day
            if overtime:
                self.rest_day_ot_hours = overtime.hours_requested
        else:
            # Only set the self.overtime_id if it's not rest day
            self.overtime_id = overtime.id

    def convert_date(self, date_leave):
        date_time_leaves = datetime.strptime(date_leave, '%Y-%m-%d %H:%M:%S')
        return to_string(context_utc(from_string(to_string(date_time_leaves)), self.env.user.tz))

    def get_leaves_reference(self):
        """Set leaves reference."""
        domain = [
            ('date_from', '<=', self.schedule_out),
            ('date_to', '>=', self.schedule_in),
            ('employee_id', '=', self.employee_id.id),
            ('state', 'in', ('validate', 'validate1')),
            ('type', '=', 'remove'),
            ('process_type', '=', False)
        ]

        leaves = self.env['hr.holidays'].search(domain)

        TIME_TO_RENDER = self.work_time_line_id.time_to_render - self.work_time_line_id.break_period
        if not self.is_suspended:
            self.leave_ids = [(6, 0, leaves.ids)]

        if self.leave_ids:
            self.is_absent = False
            self.remarks = ','.join(leaves.mapped('holiday_status_id.name'))

            if self.leave_hours >= TIME_TO_RENDER \
                    or self.leave_wop_hours >= TIME_TO_RENDER \
                    or self.ob_hours >= TIME_TO_RENDER:

                self.is_leave = True

            else:
                self.is_leave = False
                if not self.is_raw:
                    self.is_absent = True
        else:
            self.remarks = ''

    def update_attendance_holidays(self, holidays):
        """Delete and update record of holidays field."""
        reg_ids = holidays.filtered(lambda r: r.holiday_type == 'regular')
        self.reg_holiday_ids = [(6, 0, reg_ids.ids)]

        spl_ids = holidays.filtered(lambda r: r.holiday_type == 'special')
        self.spl_holiday_ids = [(6, 0, spl_ids.ids)]
        if self.spl_holiday_ids or self.reg_holiday_ids:
            self.remarks = ','.join(self.reg_holiday_ids.mapped('name')) + ','.join(self.spl_holiday_ids.mapped('name'))
        elif self.remarks and self.leave_ids:
            self.remarks = ','.join(self.leave_ids.mapped('holiday_status_id.name'))
        else:
            self.remarks = ''

    def get_holiday_reference(self):
        """Sets holiday reference."""
        domain_start = [('holiday_start', '<=', self.check_out),
                        ('holiday_end', '>=', self.check_in)]
        holidays_start = self.env['hr.attendance.holidays'].search(domain_start)

        with_work_location = holidays_start.filtered(lambda r: r.work_location_id)
        without_work_location = holidays_start.filtered(lambda r: not r.work_location_id)

        if not holidays_start:
            self.write({
                'spl_holiday_ids': False,
                'reg_holiday_ids': False,
            })
        if with_work_location:
            self.update_attendance_holidays(
                with_work_location.filtered(lambda r: r.work_location_id.id == self.employee_id.work_location_id.id))
        if without_work_location:
            self.update_attendance_holidays(without_work_location)

    @api.onchange('work_time_line_id', 'check_in', 'check_out',
                  'request_change_id', 'request_change_id.state',
                  'employee_id.disciplinary_ids')
    def get_suspension(self):
        """Set suspension remarks on employee attendances."""
        if self.employee_id and self.employee_id.disciplinary_ids:
            domain = [
                ('employee_id', '=', self.employee_id.id),
                ('penalty_id', '=', 'suspension'),
                ('start_date', '<=', self.schedule_in),
                ('end_date', '>=', self.schedule_out
                 )]
            disciplinary_actions = self.env['hr.disciplinary'].search(domain)
            domain = [
                ('employee_id', '=', self.employee_id.id),
                ('penalty_id', '=', 'suspension'),
                ('start_date', '>=', self.schedule_in),
                ('start_date', '<=', self.schedule_out)]

            disciplinary_actions2 = self.env['hr.disciplinary'].search(domain)
            disciplinary_actions2 |= disciplinary_actions

            domain = [(
                'employee_id', '=', self.employee_id.id),
                ('penalty_id', '=', 'suspension'),
                ('end_date', '>=', self.schedule_in),
                ('end_date', '<=', self.schedule_out)]

            disciplinary_actions3 = self.env['hr.disciplinary'].search(domain)
            disciplinary_actions3 |= disciplinary_actions2

            if disciplinary_actions3.ids:
                self.is_suspended = True
                self.remarks = 'SUS'

    def set_references(self):
        """Set references on attendances."""
        if self.work_time_line_id:
            rest_day = False
        if not self.work_time_line_id:
            rest_day = True

        self.get_leaves_reference()
        self.get_holiday_reference()
        self.get_overtime_reference(rest_day)

        # holidays or restday must have no work_time_line_id
        # self.onchange_reference()
        self.onchange_temp()
        self.get_suspension()

    @api.onchange('temp_in', 'temp_out', 'employee_id')
    def onchange_temp(self):
        """Checks if need to repopulate the check in or check out data on the change in or change out field."""
        if self.is_raw and self.check_in:
            self.temp_in = self.check_in

        if self.is_raw and self.check_out:
            self.temp_out = self.check_out

    @api.onchange('check_in', 'check_out', 'employee_id')
    def onchange_check_in(self):
        domain = [('start_date', '<=', self.check_in),
                  ('end_date', '>=', self.check_in),
                  ('state', '=', 'approved')]

        if self.employee_id:
            args = [('employee_id', '=', self.employee_id.id)]
            args += domain
            self.get_work_time_lines(args)
        if not self.work_time_line_id and self.employee_id.department_id:
            args = [('department_id', '=', self.employee_id.department_id.id)]
            args += domain
            self.get_work_time_lines(args)

        self.set_references()

    @api.onchange('work_time_line_id', 'overtime_id',
                  'spl_holiday_ids.holiday_start',
                  'spl_holidays.holiday_end',
                  'reg_holiday_ids.holiday_start',
                  'reg_holiday_ids.holiday_end')
    def onchange_reference(self):
        """Remove leaves reference from the attendance."""
        # if self.spl_holiday_ids or self.reg_holiday_ids or \
        # self.overtime_id.rest_day_overtime or self.rest_day_overtime:
        #    self.work_time_line_id = False
        #    self.leave_ids = False
        if self.overtime_id.rest_day_overtime or self.rest_day_overtime:
            self.work_time_line_id = False
            self.leave_ids = False

    @api.multi
    def recompute_attendance(self):
        """Binded as server action to recompute attendances."""
        for attendance in self:
            vals = {}
            vals['employee_id'] = attendance.employee_id.id
            vals['check_in'] = attendance.check_in

            if attendance.request_change_id and attendance.request_change_id.state == 'approved':
                if attendance.temp_in and attendance.temp_out:
                    vals['check_in'] = attendance.temp_in
            self._compute_attendances(attendance, vals)

    def _compute_attendances(self, res, vals):
        """Assign employee schedule and compute attendance."""
        if vals.get('check_in', False):
            domain = [('start_date', '<=', vals['check_in']),
                      ('end_date', '>=', vals['check_in']),
                      ('state', '=', 'approved')]

            if vals['employee_id']:
                args = [('employee_id', '=', vals['employee_id'])]
                args += domain
                res.get_work_time_lines(args)

            if not res.work_time_line_id and res.employee_id.department_id:
                args = [('department_id', '=', res.employee_id.department_id.id)]
                args += domain
                res.get_work_time_lines(args)

        res.set_references()

    @api.model
    def create(self, vals):
        res = super(HRAttendance, self).create(vals)
        self._compute_attendances(res, vals)
        return res

    @api.multi
    def write(self, vals):
        res = super(HRAttendance, self).write(vals)
        if not self._context.get('default_cron_schedule_time') and not self._context.get('update_from_cron') and ('spl_holiday_ids' in vals or 'reg_holiday_ids' in vals) and not self._context.get('called_from_write'):
            for record in self:
                record.with_context({'called_from_write': True}).set_references()
        return res

    def get_undertime_hours(self, date_in, date_out, required_in, required_out, lunch_break_out, lunch_break_period,
                            date_from, date_to):
        """Return undertime hours"""
        undertime = 0

        if date_out > required_in and date_out < lunch_break_out:
            undertime += abs((date_out - lunch_break_out).total_seconds() / 3600.0)
        if date_out > required_in and date_out <= lunch_break_period and lunch_break_period < required_out:
            undertime += abs((lunch_break_period - required_out).total_seconds() / 3600.0)
        if date_out > required_in and date_out < required_out and date_out > lunch_break_period:
            undertime = abs((date_out - required_out).total_seconds() / 3600.0)
            # undertime = abs(((date_from - date_to) - (date_in - date_out)).total_seconds() / 3600.0)
        return undertime

    def get_late_hours(self, grace_period, date_in, date_out, required_in, required_out, lunch_break_out,
                       lunch_break_period):
        """Return late hours"""
        late_hours = 0
        if self.work_time_line_id.break_period:
            if date_in > lunch_break_period and date_in < required_out:
                late_hours += (date_in - lunch_break_period).total_seconds() / 3600.0

            if date_in >= lunch_break_out:
                late_hours += (lunch_break_out - required_in).total_seconds() / 3600.0
            if self.is_holiday:
                late_hours = 0
            # within first period
            if date_in > grace_period and date_in < lunch_break_out:
                late_hours = (date_in - required_in).total_seconds() / 3600.0
        else:
            if date_in > grace_period:
                late_hours = (date_in - required_in).total_seconds() / 3600.0

        return late_hours

    def compute_undertime_with_leaves(self, date_in, date_out, required_in, required_out, lunch_break,
                                      lunch_break_period, date_from, date_to):
        """Compute undertime leaves."""
        TIME_TO_RENDER = self.work_time_line_id.time_to_render - self.work_time_line_id.break_period
        if date_in <= date_from <= date_out or date_from <= date_in <= date_to:
            if date_to == date_out or date_to == required_out:
                undertime = 0
            else:
                if self.is_holiday or self.is_absent or self.is_suspended or ((self.ob_hours + self.leave_hours + self.leave_wop_hours) >= TIME_TO_RENDER):
                    undertime = 0
                else:
                    date_out = min(max([date_out, date_to]), required_out)
                    undertime = self.get_undertime_hours(date_in, date_out, required_in, required_out, lunch_break,
                                                         lunch_break_period, date_from, date_to)
            return undertime

        if not (date_in <= date_from <= date_out or date_from <= date_in <= date_to):
            undertime = 0
            if date_out < date_from:
                undertime += self.get_undertime_hours(date_in, date_out, required_in, date_from, lunch_break,
                                                      lunch_break_period, date_from, date_to)
                if date_out < lunch_break and date_from < lunch_break:
                    undertime = self.get_undertime_hours(date_in, date_out, required_in, date_from, date_from,
                                                         lunch_break_period, date_from, date_to)
                if date_out > lunch_break_period and date_from > lunch_break_period:
                    undertime = self.get_undertime_hours(date_in, date_out, required_in, date_from, date_from,
                                                         lunch_break_period, date_from, date_to)
                if date_to < required_out:
                    undertime = self.get_undertime_hours(date_from, date_to, required_in, required_out, lunch_break,
                                                         lunch_break_period, date_in, date_out)
                if date_to > required_out:
                    undertime = 0
                return undertime

            if date_to < date_in:
                undertime = self.get_undertime_hours(date_from, date_to, required_in, date_in, lunch_break,
                                                     lunch_break_period, date_in, date_out)
                if date_to < lunch_break and date_in < lunch_break:
                    undertime = self.get_undertime_hours(date_from, date_to, required_in, date_in, date_in,
                                                         lunch_break_period, date_in, date_out)

                if date_to > lunch_break_period and date_in > lunch_break_period:
                    undertime = self.get_undertime_hours(date_from, date_to, required_in, date_in, date_in,
                                                         lunch_break_period, date_in, date_out)

                if date_out < required_out:
                    undertime += self.get_undertime_hours(date_in, date_out, required_in, required_out, lunch_break,
                                                          lunch_break_period, date_from, date_to)
                return undertime

    @api.depends('employee_id', 'check_in', 'check_out',
                 'overtime_id', 'overtime_id.rest_day_overtime',
                 'overtime_id.start_time', 'overtime_id.end_time',
                 'overtime_id.state', 'request_change_id', 'request_change_id.state',
                 'reg_holiday_ids.holiday_start', 'reg_holiday_ids.holiday_end',
                 'reg_holiday_ids', 'reg_holiday_ids.holiday_type',
                 'spl_holiday_ids.holiday_start', 'spl_holiday_ids.holiday_end',
                 'spl_holiday_ids', 'spl_holiday_ids.holiday_type', 'rest_day_overtime',
                 'is_absent', 'is_holiday', 'is_leave', 'is_suspended',
                 'leave_ids', 'leave_ids.state',
                 'leave_ids.date_from', 'leave_ids.date_to', 'work_time_line_id'
                 )
    def _compute_undertime_hours(self):
        """Computes undertime hours.
        Normal schedule: removes 1 hour from the normal working hours.
        """
        for attendance in self:

            if (attendance.work_time_line_id and attendance.check_out and not (attendance.reg_holiday_ids or attendance.spl_holiday_ids)
                    and not (not attendance.worked_hours and not attendance.ob_hours and attendance.leave_ids.filtered(lambda l: not l.holiday_status_id.is_ob))):

                date_in = context_timestamp(self, from_string(attendance.check_in))
                date_out = context_timestamp(self, from_string(attendance.check_out))

                # Change actual check in and check out
                if attendance.request_change_id and attendance.request_change_id.state == 'approved':
                    if attendance.temp_in and attendance.temp_out:
                        date_in = context_timestamp(self, from_string(attendance.temp_in))
                        date_out = context_timestamp(self, from_string(attendance.temp_out))

                # Not considering the seconds
                date_in = date_in.replace(second=0)
                date_out = date_out.replace(second=0)

                schedule_type = attendance.work_time_line_id.work_time_id.schedule_type

                required_in_hour, required_in_minute = float_time_convert(attendance.work_time_line_id.latest_check_in)
                required_in = date_in.replace(hour=required_in_hour, minute=required_in_minute, second=0)
                ob_leaves = attendance.leave_ids.filtered(lambda l: l.holiday_status_id.is_ob)

                if schedule_type == 'coretime':
                    if ob_leaves:
                        ob_date_in = (context_timestamp(self, from_string(min(ob_leaves.mapped('date_from'))))).replace(second=0)
                        date_in = min((context_timestamp(self, from_string(attendance.check_in))).replace(second=0), ob_date_in)


#                     obs = attendance.leave_ids.filtered(lambda r: r.holiday_status_id.is_ob)
#                     if obs:
                        # Considering Official Business date from as check in
#                         ob = [date_in] + [context_timestamp(self, from_string(dt)) for dt in obs.mapped('date_from')]
                        # Get the earliest checkin
#                         date_in = min(ob)

                    earliest_in_hour, earliest_in_minute = float_time_convert(attendance.work_time_line_id.earliest_check_in)
                    earliest_in = date_in.replace(hour=earliest_in_hour, minute=earliest_in_minute, second=0)

                    latest_in_hour, latest_in_minute = float_time_convert(attendance.work_time_line_id.latest_check_in)
                    latest_in = date_in.replace(hour=latest_in_hour, minute=latest_in_minute, second=0)

                    if date_in < earliest_in:
                        required_in = earliest_in

                    if earliest_in <= date_in <= latest_in:
                        required_in = date_in

                    if date_in >= latest_in:
                        required_in = date_in

                # check if night diff check in
                required_in = get_intersection(date_in, date_out, required_in,
                                               attendance.work_time_line_id.time_to_render)

                required_out_hour, required_out_min = float_time_convert(attendance.work_time_line_id.time_to_render)
                required_out = required_in + timedelta(hours=required_out_hour, minutes=required_out_min, seconds=0)

                earliest_hour, earliest_minute = float_time_convert(attendance.work_time_line_id.earliest_check_in)
                earliest_check_in = date_in.replace(hour=earliest_hour, minute=earliest_minute, second=0)

                lunch_break_out = required_in + timedelta(hours=4, minutes=0, seconds=0)
                break_period_hour, break_period_minute = float_time_convert(attendance.work_time_line_id.break_period)
                lunch_break_period = lunch_break_out + timedelta(hours=break_period_hour, minutes=break_period_minute,
                                                                 seconds=0)

                if schedule_type == 'flexible':
                    attendance.undertime_hours = 0

                if schedule_type in ('normal', 'coretime'):
                    if attendance.leave_ids:
                        leaves_date_from = attendance.leave_ids.filtered(lambda l: context_timestamp(self, from_string(l.date_to)) > required_in).mapped('date_from')
                        leaves_date_to = attendance.leave_ids.filtered(lambda l: context_timestamp(self, from_string(l.date_from)) < required_out).mapped('date_to')
#                         for leave in attendance.leave_ids:
#                             leaves_date_from.append(context_timestamp(self, from_string(leave.date_from)))
#                             leaves_date_to.append(context_timestamp(self, from_string(leave.date_to)))
                        date_from = leaves_date_from and context_timestamp(self, from_string(min(leaves_date_from))) or date_in
                        date_to = leaves_date_to and max([context_timestamp(self, from_string(x)) for x in leaves_date_to]) or date_out
                        attendance.undertime_hours = attendance.compute_undertime_with_leaves(date_in, date_out,
                                                                                              required_in, required_out,
                                                                                              lunch_break_out,
                                                                                              lunch_break_period,
                                                                                              date_from, date_to)
                    else:
                        if date_out < required_out:
                            holidays = self.env['hr.attendance.holidays'].search([])
                            undertime = attendance.get_undertime_hours(date_in, date_out, required_in, required_out,
                                                                       lunch_break_out, lunch_break_period, None, None)

                            if attendance.is_suspended:
                                undertime = 0
                            if attendance.is_absent:
                                undertime = 0
                            if attendance.spl_holiday_ids or attendance.reg_holiday_ids:
                                undertime = 0
                            for holiday in holidays:
                                holiday_start = context_timestamp(self, from_string(holiday.holiday_start))
                                if (date_in + timedelta(days=1)).date() == holiday_start.date():
                                    undertime = 0
                            attendance.undertime_hours = undertime
                        else:
                            attendance.undertime_hours = 0

    def night_difference(self, attendance):
        """Returns employee night shift differential hours."""
        ob_leaves = attendance.leave_ids.filtered(lambda l: l.holiday_status_id.is_ob)
        date_in = context_timestamp(self, from_string(attendance.check_in))
        date_out = context_timestamp(self, from_string(attendance.check_out))

        worktime_line = attendance.work_time_line_id
        schedule_in_hour, schedule_in_minute = float_time_convert(worktime_line.latest_check_in)
        schedule_out_hour, schedule_out_minute = float_time_convert(worktime_line.time_to_render)

        # check if schedule within the day or yesterday schedule
        schedule_in = date_in.replace(hour=schedule_in_hour, minute=schedule_in_minute, second=0)

        schedule_in = get_intersection(date_in, date_out, schedule_in, 8)

        schedule_out = schedule_in + timedelta(hours=schedule_out_hour, minutes=schedule_out_minute, seconds=0)

        if attendance.leave_ids and ob_leaves:
            if attendance.is_hide_check_time:
                check_in = []
                check_out = []
            else:
                check_in = [max([date_in, schedule_in])]
                check_out = [min([date_out, schedule_out])]
            leave_date_in = ob_leaves.filtered(lambda l: context_timestamp(self, from_string(l.date_to)) > schedule_in).mapped('date_from')
            leave_date_out = ob_leaves.filtered(lambda l: context_timestamp(self, from_string(l.date_from)) < schedule_out).mapped('date_to')
            date_from = leave_date_in and min([context_timestamp(self, from_string(x)) for x in leave_date_in]) or date_in
            date_to = leave_date_out and max([context_timestamp(self, from_string(x)) for x in leave_date_out]) or date_out
            check_in.append(date_from)
            check_out.append(date_to)
#             for leave in ob_leaves:
#                 date_from = context_timestamp(self, from_string(leave.date_from))
#                 date_to = context_timestamp(self, from_string(leave.date_to))
#                 date_from = max(date_from, schedule_in)
#                 date_to = min(date_to, schedule_out)
# #                 if date_from >= schedule_out:
# #                     date_to = min([date_out, schedule_out])
#                 check_in.append(date_from)
#                 check_out.append(date_to)
            date_in = min(check_in)
            date_out = max(check_out)
#         date_in = context_timestamp(self, from_string(date_in))
#         date_out = context_timestamp(self, from_string(date_out))
        # Change actual check in and check out
        worktime_line = attendance.work_time_line_id
        if attendance.request_change_id and attendance.request_change_id.state == 'approved':
            if attendance.temp_in and attendance.temp_out:
                date_in = context_timestamp(self, from_string(attendance.temp_in))
                date_out = context_timestamp(self, from_string(attendance.temp_out))

        # Not considering the seconds
        date_in = date_in.replace(second=0)
        date_out = date_out.replace(second=0)

        if not worktime_line:
            rendered_hours = 0
            return rendered_hours

        if ((date_out - date_in).total_seconds() / 3600.0) < 0.25:
            rendered_hours = 0
            return rendered_hours

        required_in = date_in.replace(hour=22, minute=0, second=0)
        required_in = get_intersection(date_in, date_out, required_in, 8)

        required_in = get_intersection(schedule_in, date_out, required_in, attendance.work_time_line_id.time_to_render)
        required_out_hour, required_out_minute = float_time_convert(attendance.work_time_line_id.time_to_render)

        required_out = required_in + timedelta(hours=required_out_hour, minutes=required_out_minute,
                                               seconds=0)

        NIGHT_DIFF_START = required_in
        NIGHT_DIFF_END = required_in + timedelta(hours=8)

        # GET THE HOURS AND MINUTES FORMAT
        ndiff_start_time = NIGHT_DIFF_START.strftime('%H:%M')
        ndiff_end_time = NIGHT_DIFF_END.strftime('%H:%M')
        schedule_in_time = schedule_in.strftime('%H:%M')
        schedule_out_time = schedule_out.strftime('%H:%M')

        break_period_hour = 0
        rendered_hours = 0
        if worktime_line and (
                (ndiff_start_time <= schedule_in_time <= '23:59' or '00:00' <= schedule_in_time <= ndiff_end_time)
                or (
                    ndiff_start_time <= schedule_out_time <= '23:59' or '00:00' <= schedule_out_time <= ndiff_end_time)):
            date_in = max([schedule_in, NIGHT_DIFF_START, date_in])
            date_out = min([schedule_out, NIGHT_DIFF_END, date_out])

            rendered_hours = (date_out - date_in).total_seconds() / 3600.0

            if worktime_line and NIGHT_DIFF_START < date_out <= NIGHT_DIFF_END:
                rendered_hours = (date_out - max([NIGHT_DIFF_START, date_in])).total_seconds() / 3600.0

            if date_in >= NIGHT_DIFF_END or date_out < NIGHT_DIFF_START:
                rendered_hours = 0
        if worktime_line and (attendance.reg_holiday_ids or attendance.spl_holiday_ids):
            holidays = attendance.reg_holiday_ids + attendance.spl_holiday_ids
            rendered_hours = 0
            holiday_start_list = []
            holiday_end_list = []
            for holiday in holidays:
                hours = 0
                holiday_start_list.append((context_timestamp(self, from_string(holiday.holiday_start))).replace(second=0))
                holiday_end_list.append((context_timestamp(self, from_string(holiday.holiday_end))).replace(second=0))
            holiday_start = (context_timestamp(self, from_string(min(holidays.mapped('holiday_start'))))).replace(second=0)
            holiday_end = (context_timestamp(self, from_string(max(holidays.mapped('holiday_end'))))).replace(second=0)
            if holidays:
                if holiday_start >= date_in:
                    date_from = max(date_in, NIGHT_DIFF_START, required_in)
                    date_to = min(date_out, holiday_start, NIGHT_DIFF_END)
                else:
                    date_from = max(holiday_end, NIGHT_DIFF_START, date_in, required_in)
                    date_to = min(NIGHT_DIFF_END, date_out, required_out)
            if date_in > holiday_start and date_out < holiday_end:
                rendered_hours = 0
            else:
                rendered_hours = ((date_to - date_from).total_seconds()) / 3600.0
        if not worktime_line:
            rendered_hours = 0
        if ((date_out - date_in).total_seconds() / 3600.0) < 0.25:
            rendered_hours = 0
        return rendered_hours

    @api.depends('employee_id', 'check_in', 'check_out', 'leave_ids',
                 'leave_ids.date_from', 'leave_ids.date_to', 'leave_ids.state',
                 'overtime_id', 'overtime_id.rest_day_overtime',
                 'overtime_id.start_time', 'overtime_id.end_time',
                 'overtime_id.state', 'request_change_id', 'request_change_id.state',
                 'reg_holiday_ids.holiday_start', 'reg_holiday_ids.holiday_end',
                 'reg_holiday_ids', 'reg_holiday_ids.holiday_type',
                 'spl_holiday_ids.holiday_start', 'spl_holiday_ids.holiday_end',
                 'spl_holiday_ids', 'spl_holiday_ids.holiday_type', 'rest_day_overtime',
                 'is_absent', 'is_holiday', 'is_leave', 'is_suspended'
                 )
    def _compute_night_diff_hours(self):
        """Computes night differential"""
        for attendance in self:
            if not attendance.check_out or not attendance.work_time_line_id.work_time_id.night_shift:
                attendance.night_diff_hours = 0
            elif attendance.work_time_line_id and attendance.check_out and not (
                    attendance.is_absent or attendance.is_suspended or attendance.is_leave or attendance.is_holiday and attendance.work_time_line_id.work_time_id.night_shift):
                attendance.night_diff_hours = self.night_difference(attendance)
            elif attendance.is_holiday:
                attendance.night_diff_hours = self.night_difference(attendance)
            else:
                ob_leaves = attendance.leave_ids.filtered(lambda l: l.holiday_status_id.is_ob)
                if ob_leaves:
                    attendance.night_diff_hours = self.night_difference(attendance)

    @api.depends('employee_id', 'check_in', 'check_out',
                 'overtime_id', 'overtime_id.rest_day_overtime',
                 'overtime_id.start_time', 'overtime_id.end_time',
                 'overtime_id.state', 'request_change_id', 'request_change_id.state',
                 'reg_holiday_ids.holiday_start', 'reg_holiday_ids.holiday_end',
                 'reg_holiday_ids', 'reg_holiday_ids.holiday_type',
                 'spl_holiday_ids.holiday_start', 'spl_holiday_ids.holiday_end',
                 'spl_holiday_ids', 'spl_holiday_ids.holiday_type', 'rest_day_overtime',
                 'is_absent', 'is_holiday', 'is_leave', 'is_suspended',
                 'leave_ids', 'leave_ids.state',
                 'leave_ids.date_from', 'leave_ids.date_to', 'work_time_line_id'
                 )
    def _compute_late_hours(self):
        """Computes late hours.
        If with break, it lesses the period specified
        from the numbers of hours to be rendered.
        """
        for attendance in self:
            TIME_TO_RENDER = attendance.work_time_line_id.time_to_render - attendance.work_time_line_id.break_period
            if attendance.check_out and attendance.work_time_line_id:
                date_in = context_timestamp(self, from_string(attendance.check_in))
                date_out = context_timestamp(self, from_string(attendance.check_out))
                schedule_type = attendance.work_time_line_id.work_time_id.schedule_type

                if schedule_type == 'flexible':
                    attendance.late_hours = 0

                # Change actual check in and check out
                if attendance.request_change_id and attendance.request_change_id.state == 'approved':
                    if attendance.temp_in and attendance.temp_out:
                        date_in = context_timestamp(self, from_string(attendance.temp_in))
                        date_out = context_timestamp(self, from_string(attendance.temp_out))

                # Not considering the seconds
                date_in = date_in.replace(second=0)
                date_out = date_out.replace(second=0)

                if schedule_type in ('normal', 'coretime'):

                    required_in_hour, required_in_minute = float_time_convert(
                        attendance.work_time_line_id.latest_check_in)
                    required_in = date_in.replace(hour=required_in_hour, minute=required_in_minute, second=0)

                    # check if night diff check in
                    required_in = get_intersection(date_in, date_out, required_in,
                                                   attendance.work_time_line_id.time_to_render)

                    required_out_hour, required_out_minute = float_time_convert(
                        attendance.work_time_line_id.time_to_render)
                    required_out = required_in + timedelta(hours=required_out_hour, minutes=required_out_minute,
                                                           seconds=0)

                    grace_period_hour, grace_period_minute = float_time_convert(
                        attendance.work_time_line_id.work_time_id.grace_period)
                    grace_period = required_in + timedelta(hours=grace_period_hour, minutes=grace_period_minute,
                                                           seconds=0)

                    lunch_break_out = required_in + timedelta(hours=4, minutes=0, seconds=0)
                    break_period_hour, break_period_minute = float_time_convert(
                        attendance.work_time_line_id.break_period)
                    lunch_break_period = lunch_break_out + timedelta(hours=break_period_hour,
                                                                     minutes=break_period_minute, seconds=0)
                    ob_leaves = attendance.leave_ids.filtered(lambda l: l.holiday_status_id.is_ob)
                    if attendance.work_time_line_id.work_time_id.night_shift:
                        attendance.late_hours = attendance.get_late_hours(grace_period, date_in, date_out, required_in,
                                                                          required_out, lunch_break_out, lunch_break_period)
                    is_holiday = False
                    if attendance.reg_holiday_ids or attendance.spl_holiday_ids:
                        holidays = attendance.reg_holiday_ids + attendance.spl_holiday_ids
                        holiday_start = min(holidays.mapped('holiday_start'))
                        holiday_end = max(holidays.mapped('holiday_end'))

                        holiday_start = (context_timestamp(self, from_string(holiday_start))).replace(second=0)
                        holiday_end = (context_timestamp(self, from_string(holiday_end))).replace(second=0)
                        if required_in >= holiday_start and required_out <= holiday_end:
                            is_holiday = True
                    if attendance.leave_ids:
                        late_hours = 0
                        leaves_date_in = []
                        leaves_date_out = []
                        if not attendance.is_hide_check_time:
                            leaves_date_in.append(date_in)
                        leave_date_in = attendance.leave_ids.filtered(lambda l: context_timestamp(self, from_string(l.date_to)) > required_in).mapped('date_from')
                        leave_date_out = attendance.leave_ids.filtered(lambda l: context_timestamp(self, from_string(l.date_from)) < required_out).mapped('date_to')
#                         for leave in attendance.leave_ids:
#                             leaves_date_in.appeleaves_date_innd(context_timestamp(self, from_string(leave.date_from)))
#                             leaves_date_out.append(context_timestamp(self, from_string(leave.date_to)))
#                         leaves_date_in.append(context_timestamp(self, from_string(max(leave_date_in))))
                        if leave_date_in:
                            date_from = min([context_timestamp(self, from_string(x)) for x in leave_date_in])
                            date_in = attendance.is_hide_check_time and date_from or min(date_from, date_in)
                        attendance.late_hours = attendance.get_late_hours(grace_period, date_in, date_out, required_in,
                                                                          required_out, lunch_break_out, lunch_break_period)

                    elif not is_holiday and not (not attendance.worked_hours and not attendance.ob_hours
                                                 and attendance.leave_ids.filtered(lambda l: not l.holiday_status_id.is_ob)):
                        if attendance.leave_ids:
                            # considered absent
                            late_hours = 0
                            leaves_date_in = [date_in]
                            leaves_date_out = [date_out]
                            for leave in attendance.leave_ids:
                                leaves_date_in.append(context_timestamp(self, from_string(leave.date_from)))
                                leaves_date_out.append(context_timestamp(self, from_string(leave.date_to)))

                            date_in = min(leaves_date_in)
                            if grace_period < date_in and not (date_in >= required_out):
                                late_hours = 0
                                if attendance.work_time_line_id.break_period:

                                    late_hours = attendance.get_late_hours(grace_period, date_in, date_out, required_in,
                                                                           required_out, lunch_break_out, lunch_break_period)
                                elif attendance.is_holiday or attendance.is_absent or attendance.is_suspended or ((attendance.ob_hours + attendance.leave_hours + attendance.leave_wop_hours) >= TIME_TO_RENDER):
                                    late_hours = 0
                                else:
                                    late_hours = (date_in - required_in).total_seconds() / 3600.0
                                attendance.late_hours = late_hours

                        else:
                            # considered absent
                            late_hours = 0
                            if date_in >= required_out:
                                #                                 attendance.absent_hours = attendance.worked_hours
                                #                                 attendance.is_absent = True
                                attendance.late_hours = 0
                                attendance.remarks = 'ABS'
#                                 attendance.worked_hours = 0
                            # No late on suspension
                            if attendance.is_suspended:
                                attendance.late_hours = 0
                                continue

                            if grace_period < date_in and not (date_in >= required_out) and not attendance.is_absent:
                                late_hours = 0

                                if attendance.work_time_line_id.break_period:
                                    late_hours = attendance.get_late_hours(grace_period, date_in, date_out, required_in,
                                                                           required_out, lunch_break_out, lunch_break_period)
                                else:
                                    late_hours = (date_in - required_in).total_seconds() / 3600.0
                                if attendance.spl_holiday_ids or attendance.reg_holiday_ids:
                                    late_hours = late_hours
                                attendance.late_hours = late_hours

    def get_holiday_hours(self, attendance, required_in, required_out):
        """Check if it is a whole day holiday."""
        worked_hours = 0
        for spl_holiday in attendance.spl_holiday_ids:
            start_time = context_timestamp(self, from_string(spl_holiday.holiday_start))
            end_time = context_timestamp(self, from_string(spl_holiday.holiday_end))

            if start_time <= required_in <= required_out <= end_time:
                worked_hours = 0

        for reg_holiday in attendance.reg_holiday_ids:
            start_time = context_timestamp(self, from_string(reg_holiday.holiday_start))
            end_time = context_timestamp(self, from_string(reg_holiday.holiday_end))

            if start_time <= required_in <= required_out <= end_time:
                worked_hours = 0
        return worked_hours

    @api.depends('employee_id', 'check_in', 'check_out',
                 'overtime_id', 'overtime_id.rest_day_overtime',
                 'overtime_id.start_time', 'overtime_id.end_time',
                 'overtime_id.state', 'request_change_id', 'request_change_id.state',
                 'reg_holiday_ids.holiday_start', 'reg_holiday_ids.holiday_end',
                 'reg_holiday_ids', 'reg_holiday_ids.holiday_type',
                 'spl_holiday_ids.holiday_start', 'spl_holiday_ids.holiday_end',
                 'spl_holiday_ids', 'spl_holiday_ids.holiday_type', 'rest_day_overtime',
                 'is_absent', 'is_holiday', 'is_leave', 'is_suspended',
                 'leave_ids', 'leave_ids.state',
                 'leave_ids.date_from', 'leave_ids.date_to', 'work_time_line_id'
                 )
    def _compute_night_diff_overtime_hours(self):
        """Computes night overtime hours. """
        for attendance in self:
            TIME_TO_RENDER = attendance.work_time_line_id.time_to_render - attendance.work_time_line_id.break_period
            if attendance.check_out and attendance.overtime_id and attendance.overtime_id.state == 'approved':
                date_in = context_timestamp(self, from_string(attendance.check_in))
                date_out = context_timestamp(self, from_string(attendance.check_out))

                # Change actual check in and check out
                if attendance.request_change_id and attendance.request_change_id.state == 'approved':
                    if attendance.temp_in and attendance.temp_out:
                        date_in = context_timestamp(self, from_string(attendance.temp_in))
                        date_out = context_timestamp(self, from_string(attendance.temp_out))

                    # Not considering the seconds
                    date_in = date_in.replace(second=0)
                    date_out = date_out.replace(second=0)

                    start_time = context_timestamp(self, from_string(attendance.overtime_id.start_time))
                    end_time = context_timestamp(self, from_string(attendance.overtime_id.end_time))
                    days = (date_out - date_in).days

                    # Build night shift schedule
                    # To determine overlapping night diff schedule
                    night_diff_list = []
                    for day in range(days + 1):
                        required_in = date_in.replace(hour=22, minute=0, second=0) + timedelta(days=day)

                        required_in = get_intersection(date_in, date_out, required_in,
                                                       attendance.work_time_line_id.time_to_render)
                        required_out = required_in + timedelta(hours=TIME_TO_RENDER)
                        night_diff_list.append((required_in, required_out))

                    night_diff_overtime = 0
                    for night_diff_schedule in night_diff_list:
                        NIGHT_DIFF_START, NIGHT_DIFF_END = night_diff_schedule

                        if date_out > NIGHT_DIFF_START:
                            night_diff_overtime += (min([NIGHT_DIFF_END, date_out, end_time]) - max(
                                [NIGHT_DIFF_START, date_in, start_time])).total_seconds() / 3600.0

                    # overtime should be on day of attendance
                    if night_diff_overtime > 0:
                        attendance.night_diff_ot_hours = night_diff_overtime
                else:
                    ob_leave = attendance.leave_ids.filtered(lambda l: l.holiday_status_id.is_ob)
                    ob_leave = ob_leave and ob_leave[0] or False
                    if ob_leave:
                        ob_start = context_timestamp(self, from_string(ob_leave.date_from))
                        date_in = min(ob_start, date_in)
                        ob_to = context_timestamp(self, from_string(ob_leave.date_to))
                        date_out = max(ob_to, date_out)
                    ot_start = context_timestamp(self, from_string(attendance.overtime_id.start_time))
                    ot_end = context_timestamp(self, from_string(attendance.overtime_id.end_time))
                    start = max(date_in, ot_start)
                    end = min(date_out, ot_end)
                    night_diff_start = context_timestamp(self, from_string(attendance.overtime_id.start_time)).replace(
                        hour=22, minute=0)
                    night_diff_end = night_diff_start + timedelta(hours=8)
                    morning_diff_start = context_timestamp(self, from_string(attendance.overtime_id.start_time) - timedelta(days=1)).replace(hour=22, minute=0)
                    morning_diff_end = morning_diff_start + timedelta(hours=8)
                    night_diff_min = []
                    night_diff_mins = 0
                    while start < end:
                        if night_diff_start <= start < night_diff_end or morning_diff_start <= start < morning_diff_end:
                            night_diff_min.append(start)
                            night_diff_mins += 1
                        start += timedelta(minutes=1)
                    number_of_nd = timedelta(minutes=len(night_diff_min))
                    attendance.night_diff_ot_hours = convert_time_to_float(number_of_nd)
                    reg_nd = max(0, (attendance.night_diff_hours - attendance.night_diff_ot_hours))

    @api.constrains('check_in', 'check_out', 'employee_id')
    def _check_validity(self):
        if self.env.context.get('default_cron_schedule_time', False):
            return
        for attendance in self:
            attendance_overtime = self.env['hr.attendance.overtime']
            if not attendance.check_in and not attendance.check_out and not attendance.schedule_out and not attendance.schedule_in and attendance_overtime.offset:
                # Checks if the creating of attendance is offset
                if attendance_overtime.offset:
                    holidays = self.env['hr.holidays']
                    holidays._check_attendance_offset()
                else:
                    raise ValidationError(_("Cannot create new attendance record for %(empl_name)s, the employee has no schedule time and check in time.") % {
                        'empl_name': attendance.employee_id.name_related,
                    })           
        return super(HRAttendance, self)._check_validity()

    @api.multi
    def _compute_hide_check_time(self):
        for attendance in self:
            if attendance.check_in == attendance.check_out:
                attendance.is_hide_check_time = True
            else:
                attendance.is_hide_check_time = False

    @api.depends('employee_id', 'check_in', 'check_out',
                 'overtime_id', 'overtime_id.rest_day_overtime',
                 'overtime_id.start_time', 'overtime_id.end_time',
                 'overtime_id.state', 'request_change_id', 'request_change_id.state',
                 'reg_holiday_ids.holiday_start', 'reg_holiday_ids.holiday_end',
                 'reg_holiday_ids', 'reg_holiday_ids.holiday_type',
                 'spl_holiday_ids.holiday_start', 'spl_holiday_ids.holiday_end',
                 'spl_holiday_ids', 'spl_holiday_ids.holiday_type', 'rest_day_overtime',
                 'is_absent', 'is_holiday', 'is_leave', 'is_suspended', 'work_time_line_id'
                 )
    def _compute_schedule(self):
        """Computes employee required schedule."""
        for attendance in self:
            if attendance.work_time_line_id and attendance.check_in and attendance.check_out:

                date_in = context_timestamp(self, from_string(attendance.check_in))
                date_out = context_timestamp(self, from_string(attendance.check_out))

                # Change actual check in and check out
                if attendance.request_change_id and attendance.request_change_id.state == 'approved':
                    if attendance.temp_in and attendance.temp_out:
                        date_in = context_timestamp(self, from_string(attendance.temp_in))
                        date_out = context_timestamp(self, from_string(attendance.temp_out))

                # Not considering the seconds
                date_in = date_in.replace(second=0)
                date_out = date_out.replace(second=0)

                schedule_type = attendance.work_time_line_id.work_time_id.schedule_type

                required_in_hour, required_in_minute = float_time_convert(attendance.work_time_line_id.latest_check_in)
                required_in = date_in.replace(hour=required_in_hour, minute=required_in_minute, second=0)

                if schedule_type == 'coretime':

                    obs = attendance.leave_ids.filtered(lambda r: r.holiday_status_id.is_ob)
                    if obs:
                        # Considering Official Business date from as check in
                        ob = [date_in] + [context_timestamp(self, from_string(dt)) for dt in obs.mapped('date_from')]
                        # Get the earliest checkin
                        date_in = min(ob)

                    earliest_in_hour, earliest_in_minute = float_time_convert(
                        attendance.work_time_line_id.earliest_check_in)
                    earliest_in = date_in.replace(hour=earliest_in_hour, minute=earliest_in_minute, second=0)

                    latest_in_hour, latest_in_minute = float_time_convert(attendance.work_time_line_id.latest_check_in)
                    latest_in = date_in.replace(hour=latest_in_hour, minute=latest_in_minute, second=0)

                    if date_in < earliest_in:
                        required_in = earliest_in

                    if date_in >= earliest_in and date_in <= latest_in:
                        required_in = date_in

                    if date_in >= latest_in:
                        required_in = latest_in

                required_in = get_intersection(date_in, date_out, required_in, attendance.work_time_line_id.time_to_render)

                required_out_hour, required_out_minute = float_time_convert(attendance.work_time_line_id.time_to_render)
                required_out = required_in + timedelta(hours=required_out_hour, minutes=required_out_minute, seconds=0)

                attendance.schedule_in = to_string(context_utc(from_string(to_string(required_in)), self.env.user.tz))
                attendance.schedule_out = to_string(context_utc(from_string(to_string(required_out)), self.env.user.tz))
            elif self.env.context.get('default_cron_schedule_time') and attendance.work_time_line_id:
                # to create absent record from cron if not biometric entry for employee
                required_in_hour, required_in_minute = float_time_convert(attendance.work_time_line_id.latest_check_in)
                actual_datetime = context_timestamp(self, fields.Datetime.from_string(fields.Datetime.to_string(
                    self.env.context['default_cron_schedule_time'])))
                schedule_in_date = actual_datetime.replace(hour=required_in_hour, minute=required_in_minute)
                schedule_in_date = get_intersection(actual_datetime, actual_datetime, schedule_in_date,
                                                    attendance.work_time_line_id.time_to_render)
                required_out_hour, required_out_minute = float_time_convert(attendance.work_time_line_id.time_to_render)
                schedule_out_date = schedule_in_date + timedelta(
                    hours=required_out_hour, minutes=required_out_minute, seconds=0)
                attendance.schedule_in = to_string(context_utc(from_string(to_string(schedule_in_date)), self.env.user.tz))
                attendance.schedule_out = to_string(context_utc(from_string(to_string(schedule_out_date)), self.env.user.tz))

    def get_ob_hours(self, date_in=None, date_out=None, required_in=None, required_out=None, break_time=0,
                     lunch_break=False, lunch_break_period=False):
        if not date_out or not date_in or not required_in or not required_out:
            return 0
        if break_time and lunch_break and lunch_break_period:
            ob_hours = 0
            if date_in < lunch_break:
                ob_hours += (min([date_out, lunch_break]) - max(
                    [required_in, date_in])).total_seconds() / 3600.0
            if lunch_break_period < required_out and date_out > lunch_break_period:
                ob_hours += (min([date_out, required_out]) - max(
                    [lunch_break_period, date_in, required_in])).total_seconds() / 3600.0
            if lunch_break <= date_in <= lunch_break_period:
                ob_hours = (min([date_out, required_out]) - max(
                    [date_in, lunch_break_period])).total_seconds() / 3600.0
            if lunch_break <= date_in <= date_out <= lunch_break_period:
                ob_hours = 0
        else:
            ob_hours = (min([date_out, required_out]) - max(
                [date_in, required_in])).total_seconds() / 3600.0
        return ob_hours

    def get_before_check_in(self, check_in, week_days):
        date_in = check_in - timedelta(days=1)
        if date_in.strftime('%A').lower() in week_days:
            return date_in
        return self.get_before_check_in(date_in, week_days)

    def get_after_check_out(self, check_out, week_days):
        date = check_out + timedelta(days=1)
        if date.strftime('%A').lower() in week_days:
            return date
        return self.get_after_check_out(date, week_days)

    def _validate_holidays(self, schedule_week_days):
        #       HOLIDAY SETTTING COMPUTATION
        absent_hours = 0.0
        check_in = self.schedule_in and datetime.strptime(self.schedule_in, '%Y-%m-%d %H:%M:%S').date()
        check_out = self.schedule_out and datetime.strptime(self.schedule_out, '%Y-%m-%d %H:%M:%S').date()
        attendances = self.env['hr.attendance'].search([('employee_id', '=', self.employee_id.id)])
        before_check_in = check_in and self.get_before_check_in(check_in, schedule_week_days)
        after_check_out = check_out and self.get_after_check_out(check_out, schedule_week_days)
        leaves = self.leave_ids.filtered(lambda l: not l.holiday_status_id.is_ob)
        attendance_before = attendances.filtered(lambda l: l.schedule_in and datetime.strptime(l.schedule_in, '%Y-%m-%d %H:%M:%S').date() == before_check_in and l.worked_hours > 0)
        attendance_after = attendances.filtered(lambda l: l.schedule_in and datetime.strptime(l.schedule_in, '%Y-%m-%d %H:%M:%S').date() == after_check_out and l.worked_hours > 0)
        holiday_setting = self.env['hr.holiday.setting']
        if self.reg_holiday_ids:
            holiday_setting = self.env['hr.holiday.setting'].search([('holiday_type', '=', 'regular')], limit=1)
        elif self.spl_holiday_ids:
            holiday_setting = self.env['hr.holiday.setting'].search([('holiday_type', '=', 'special')], limit=1)
        if holiday_setting:
            if not attendance_before or attendance_after:
                self.ob_hours = 0.0
                self.leave_hours = 0.0
                self.leave_wop_hours = 0.0
            self.worked_hours = 0.0
            self.late_hours = 0.0
            self.night_diff_hours = 0.0
            self.leave_hours = 0.0
            self.leave_wop_hours = 0.0
            if holiday_setting:
                if holiday_setting.before and holiday_setting.after and ((not attendance_before or not attendance_after) and not leaves):
                    absent_hours = TIME_TO_RENDER
                elif holiday_setting.before and (not attendance_before and not leaves):
                    absent_hours = TIME_TO_RENDER
                elif holiday_setting.after and (not attendance_after and not leaves):
                    absent_hours = TIME_TO_RENDER
#         elif attendance_before.is_absent == True or attendance_after.is_absent == True:
#             absent_hours = TIME_TO_RENDER
        self.worked_hours = 0.0
        return absent_hours
    # FOR REFRACTORING
    @api.depends('employee_id', 'check_in', 'check_out',
                 'work_time_line_id', 'is_absent',
                 'overtime_id', 'overtime_id.rest_day_overtime',
                 'overtime_id.start_time', 'overtime_id.end_time', 'leave_ids',
                 'leave_ids.state', 'leave_ids.date_from', 'leave_ids.date_to',
                 'overtime_id.state', 'request_change_id', 'request_change_id.state',
                 'reg_holiday_ids.holiday_start', 'reg_holiday_ids.holiday_end',
                 'reg_holiday_ids', 'reg_holiday_ids.holiday_type',
                 'spl_holiday_ids.holiday_start', 'spl_holiday_ids.holiday_end',
                 'spl_holiday_ids', 'spl_holiday_ids.holiday_type', 'rest_day_overtime',
                 'is_holiday', 'is_leave', 'is_suspended')
    def _worked_hours_computation(self):
        for attendance in self:
            TIME_TO_RENDER = attendance.work_time_line_id.time_to_render - attendance.work_time_line_id.break_period
            night_shift = attendance.work_time_line_id.work_time_id.night_shift
            ob_leaves = attendance.leave_ids.filtered(lambda l: l.holiday_status_id.is_ob)
            leaves = attendance.leave_ids.filtered(lambda l: not l.holiday_status_id.is_ob)
            """Get checkin/checkout time"""
            # if (attendance.leave_ids and (ob_leaves or leaves) and not attendance.spl_holiday_ids and not attendance.reg_holiday_ids
            #     and attendance.work_time_line_id and attendance.check_in and attendance.check_out):
            #     for leave in ob_leaves:
            #         ob_date_in = (context_timestamp(self, from_string(leave.date_from))).replace(second=0)
            #         ob_date_out = (context_timestamp(self, from_string(leave.date_to))).replace(second=0)
            #     ob_hours = attendance.get_ob_hours(ob_date_in, ob_date_out):

            # for leave in leaves:
            #     date_in = (context_timestamp(self, from_string(leave.date_from))).replace(second=0)
            #     date_out = (context_timestamp(self, from_string(leave.date_to))).replace(second=0)
            attendance.is_absent = False
            if (not attendance.check_in or not attendance.check_out) and not ob_leaves:
                attendance.is_absent = True
                attendance.absent_hours = TIME_TO_RENDER
                return
            if attendance.check_in and attendance.check_out:
                date_in = (context_timestamp(self, from_string(attendance.check_in))).replace(second=0)
                date_out = (context_timestamp(self, from_string(attendance.check_out))).replace(second=0)
            if attendance.is_hide_check_time and ob_leaves:
                date_in = (context_timestamp(self, from_string(min(ob_leaves.mapped('date_from'))))).replace(second=0)
                date_out = (context_timestamp(self, from_string(max(ob_leaves.mapped('date_to'))))).replace(second=0)
            """Required_in/required_out time"""
            required_in_hour, required_in_minute = float_time_convert(attendance.work_time_line_id.latest_check_in)
            required_in = date_in.replace(hour=required_in_hour, minute=required_in_minute, second=0)
            leave_wp_hours = 0
            leave_wop_hours = 0
            ob_hours = 0
            schedule_type = attendance.work_time_line_id.work_time_id.schedule_type
            if schedule_type == 'coretime':
                if ob_leaves:
                    ob_date_in = (context_timestamp(self, from_string(min(ob_leaves.mapped('date_from'))))).replace(second=0)
                    if ob_date_in:
                        date_in = min((context_timestamp(self, from_string(attendance.check_in))).replace(second=0), ob_date_in)
                    else:
                        date_in = (context_timestamp(self, from_string(attendance.check_in))).replace(second=0)
                earliest_in_hour, earliest_in_minute = float_time_convert(
                    attendance.work_time_line_id.earliest_check_in)
                earliest_in = date_in.replace(hour=earliest_in_hour, minute=earliest_in_minute, second=0)

                latest_in_hour, latest_in_minute = float_time_convert(attendance.work_time_line_id.latest_check_in)
                latest_in = date_in.replace(hour=latest_in_hour, minute=latest_in_minute, second=0)

                if date_in < earliest_in:
                    required_in = earliest_in
                if date_in >= earliest_in and date_in <= latest_in:
                    required_in = date_in
                if date_in >= latest_in:
                    required_in = latest_in
            if attendance.reg_holiday_ids or attendance.spl_holiday_ids:
                holidays = attendance.reg_holiday_ids + attendance.spl_holiday_ids
                holiday_start = (context_timestamp(self, from_string(min(holidays.mapped('holiday_start'))))).replace(second=0)
                holiday_end = (context_timestamp(self, from_string(max(holidays.mapped('holiday_end'))))).replace(second=0)
            else:
                holiday_start = False
                holiday_end = False

            # check if night diff check in
            required_in = get_intersection(date_in, date_out, required_in,
                                           attendance.work_time_line_id.time_to_render)
            required_out_hour, required_out_minute = float_time_convert(attendance.work_time_line_id.time_to_render)
            required_out = required_in + timedelta(hours=required_out_hour, minutes=required_out_minute, seconds=0)
            actual_time_in = date_in
            actual_time_out = date_out

            """Worked Hours with Lunch break"""
            if attendance.work_time_line_id.break_period:
                break_period_hour, break_period_minute = float_time_convert(
                    attendance.work_time_line_id.break_period)
                break_time = (break_period_hour + break_period_minute)
                lunch_break = required_in + timedelta(hours=4, minutes=0, seconds=0)
                lunch_break_period = lunch_break + timedelta(hours=break_period_hour, minutes=break_period_minute,
                                                             seconds=0)
                worked_hours = 0
                if date_in < lunch_break:
                    worked_hours += (min([date_out, lunch_break]) - max(
                        [date_in, required_in])).total_seconds() / 3600.0
                if lunch_break_period < required_out and date_out > lunch_break_period:
                    worked_hours += (min([date_out, required_out]) - max(
                        [lunch_break_period, required_in])).total_seconds() / 3600.0
                if date_in > lunch_break_period and date_in < required_out:
                    worked_hours = (min([date_out, required_out]) - max(
                        [date_in, lunch_break_period])).total_seconds() / 3600.0
                if lunch_break <= date_in <= lunch_break_period:
                    worked_hours = (min([date_out, required_out]) - max(
                        [date_in, lunch_break_period])).total_seconds() / 3600.0
                if lunch_break <= date_in <= date_out <= lunch_break_period:
                    worked_hours = 0
            else:
                break_time = 0
                lunch_break = False
                lunch_break_period = False
                if holiday_start and holiday_end:
                    if holiday_start >= date_in:
                        in_date = max(date_in, required_in)
                        out_date = min(date_out, holiday_start, required_out)
                    else:
                        in_date = max(holiday_end, date_in, required_in)
                        out_date = min(date_out, required_out)
                else:
                    in_date = max([date_in, required_in])
                    out_date = min([date_out, required_out])
                worked_hours = (out_date - in_date).total_seconds() / 3600.0
            if attendance.work_time_line_id:
                work_time = attendance.work_time_line_id.work_time_id
            else:
                work_time = self.env['hr.employee.schedule.work_time'].search([
                    ('employee_id', '=', attendance.employee_id.id), ('state', '=', 'approved')],
                    order="priority", limit=1)
            schedule_week_days = work_time.work_time_lines.mapped('days_of_week')
            if attendance.check_in and attendance.check_out and attendance.work_time_line_id:
                """Worked Hours"""
                if worked_hours < 0.25:
                    attendance.worked_hours = 0
                elif not attendance.is_hide_check_time and worked_hours > TIME_TO_RENDER:
                    attendance.is_absent = False
                    attendance.worked_hours = TIME_TO_RENDER
                elif not attendance.is_hide_check_time:
                    attendance.is_absent = False
                    attendance.worked_hours = worked_hours
                """OB Hours"""
                if attendance.leave_ids and ob_leaves and not attendance.reg_holiday_ids and not attendance.spl_holiday_ids:
                    """OB hours if  no holiday"""
                    for leave in ob_leaves:
                        ob_date_in = (context_timestamp(self, from_string(leave.date_from))).replace(second=0)
                        ob_date_out = (context_timestamp(self, from_string(leave.date_to))).replace(second=0)
                    ob_hours = attendance.get_ob_hours(ob_date_in, ob_date_out, required_in, required_out,
                                                       break_time, lunch_break, lunch_break_period)
                    if required_in <= ob_date_in <= required_out:
                        # attendance.worked_hours = 0
                        attendance.ob_hours = ob_hours
                        attendance.is_absent = False
                    if ob_hours < 0.25:
                        attendance.ob_hours = 0
                    elif ob_hours > TIME_TO_RENDER:
                        attendance.is_absent = False
                        attendance.ob_hours = TIME_TO_RENDER
                        ob_hours = TIME_TO_RENDER
                    else:
                        attendance.is_absent = False
                        attendance.ob_hours = ob_hours
                """OB hours if holiday (night shift)"""
                if (attendance.reg_holiday_ids or attendance.spl_holiday_ids) and night_shift:
                    if attendance.leave_ids and ob_leaves:
                        leave_date_in = []
                        leave_date_out = []
                        for leave in ob_leaves:
                            leave_date_in.append(context_timestamp(self, from_string(leave.date_from)))
                            leave_date_out.append(context_timestamp(self, from_string(leave.date_to)))
                        date_in = min(leave_date_in)
                        date_out = max(leave_date_out)
                    if attendance.reg_holiday_ids or attendance.spl_holiday_ids:
                        """With lunch break"""
                        if attendance.work_time_line_id.break_period:
                            hours = 0
                            if date_in < lunch_break:
                                hours += (min([date_out, lunch_break, holiday_end]) - max(
                                    [date_in, required_in])).total_seconds() / 3600.0
                            if lunch_break_period < required_out and date_out > lunch_break_period:
                                hours += (min([holiday_end, required_out, date_out]) - max(
                                    [lunch_break_period, required_in, holiday_start, date_in])).total_seconds() / 3600.0
                            if holiday_end < date_out:
                                if date_in < lunch_break:
                                    hours = 0
                                    hours += (min([date_out, lunch_break]) - max(
                                        [date_in, required_in, holiday_end])).total_seconds() / 3600.0
                                if lunch_break_period < required_out and date_out > lunch_break_period:
                                    hours += (min([required_out, date_out]) - max(
                                        [lunch_break_period, date_in])).total_seconds() / 3600.0
                            if holiday_end > date_out:
                                hours = (min([date_out, lunch_break, holiday_start]) - max(
                                    [date_in, required_in])).total_seconds() / 3600.0
                            if date_in > lunch_break_period and date_in < required_out:
                                hours = (min([date_out, required_out]) - max(
                                    [date_in, lunch_break_period])).total_seconds() / 3600.0
                            if lunch_break <= date_in <= lunch_break_period:
                                hours = (min([date_out, required_out]) - max(
                                    [date_in, lunch_break_period])).total_seconds() / 3600.0
                            if date_in > holiday_start and holiday_end > date_out:
                                hours = 0
                            if lunch_break <= date_in <= date_out <= lunch_break_period:
                                hours = 0
                        else:
                            break_time = 0
                            lunch_break = False
                            lunch_break_period = False
                            if holiday_start >= date_in:
                                in_date = max(date_in, required_in)
                                out_date = min(date_out, holiday_start, required_out)
                            else:
                                in_date = max(holiday_end, date_in, required_in)
                                out_date = min(date_out, required_out)
                            hours = (out_date - in_date).total_seconds() / 3600.0
                        if attendance.leave_ids and ob_leaves:
                            attendance.ob_hours = hours or 0
                            ob_hours = hours
                        else:
                            attendance.worked_hours = hours or 0
                    # elif attendance.reg_holiday_ids or attendance.spl_holiday_ids:
                    #     attendance.worked_hours = 0
                """Leave Hours"""
                if attendance.leave_ids and leaves:
                    ob_hours = attendance.ob_hours
                    worked_leave_hours = 0
                    for leave in leaves:
                        leave_hours = attendance.calculate_leave_hours(leave, lunch_break, lunch_break_period,
                                                                       date_in, date_out, required_in, required_out, worked_leave_hours)
                        leave_wp_hours += leave_hours['leave_wp_hours']
                        leave_wop_hours += leave_hours['leave_wop_hours']
                        worked_leave_hours = leave_hours['worked_hours']
                    attendance.leave_hours = leave_wp_hours > 0 and leave_wp_hours or 0
                    attendance.leave_wop_hours = leave_wop_hours > 0 and leave_wop_hours or 0
                    if not attendance.is_absent and not attendance.is_holiday and not attendance.is_leave and not attendance.is_ob:
                        hours = worked_leave_hours - ob_hours
                        attendance.worked_hours = hours > 0 and hours or 0
                        attendance.absent_hours = 0
            """Overtime Calculation"""
            min_overtime_hours = float(self.env['ir.config_parameter'].get_param('minimum.overtime.hours', '1'))
            if attendance.overtime_id and attendance.overtime_id.state == 'approved':
                date_in = (context_timestamp(self, from_string(attendance.check_in))).replace(second=0)
                date_out = (context_timestamp(self, from_string(attendance.check_out))).replace(second=0)
                if attendance.leave_ids and ob_leaves:
                    for leave in ob_leaves:
                        ob_date_in = (context_timestamp(self, from_string(leave.date_from))).replace(second=0)
                        ob_date_out = (context_timestamp(self, from_string(leave.date_to))).replace(second=0)

                        date_in = min([date_in, ob_date_in]) or date_in
                        date_out = max([date_out, ob_date_out]) or date_out

                overtime_start_time = (context_timestamp(self, from_string(attendance.overtime_id.start_time))).replace(second=0)
                overtime_end_time = (context_timestamp(self, from_string(attendance.overtime_id.end_time))).replace(second=0)
                min_end = min([date_out, overtime_end_time])
                max_start = max([date_in, overtime_start_time])
                overtime = (min_end - max_start).total_seconds() / 3600.0
                """Regular Ot"""
                if overtime > min_overtime_hours:
                    #                     if attendance.work_time_line_id:
                    #                         work_time = attendance.work_time_line_id.work_time_id
                    #                     else:
                    #                         work_time = self.env['hr.employee.schedule.work_time'].search([(
                    #                             'employee_id', '=', attendance.employee_id.id),('state', '=', 'approved')], order="priority", limit=1)
                    #                     schedule_week_days = work_time.work_time_lines.mapped('days_of_week')
                    restday_overtime_after = 0
                    restday_overtime_before = 0
                    restday_overtime = 0
                    midnight = min_end.replace(hour=0, minute=0)
                    if not overtime_end_time.strftime('%A').lower() in schedule_week_days and attendance.work_time_line_id:
                        restday_overtime_after = (min_end - max([required_out, midnight])).total_seconds() / 3600.0
                        overtime_hours = (midnight - max_start).total_seconds() / 3600.0
                    elif not attendance.work_time_line_id:
                        restday_overtime_before = overtime
                    # if not overtime_start_time.strftime('%A').lower() in schedule_week_days:
                    #     restday_overtime_before = (midnight - max_start).total_seconds() / 3600.0
                    #     overtime_hours = (min_end - midnight).total_seconds() / 3600.0
                    if attendance.overtime_id.with_break:
                        ot_break_period = attendance.overtime_id.break_period + attendance.overtime_id.break_period2
                        overtime -= ot_break_period
                    if holiday_start and holiday_end and overtime_start_time > holiday_start and overtime_start_time < holiday_end:
                        overtime_hours = 0
                    else:
                        overtime_hours = overtime
                    restday_overtime = restday_overtime_after + restday_overtime_before
                    if restday_overtime and attendance.overtime_id.with_break:
                        # overtime_hours = overtime_hours - break_time
                        restday_overtime = restday_overtime - ot_break_period
                    if restday_overtime > HOURS_PER_DAY:
                        attendance.rest_day_hours = HOURS_PER_DAY
                        attendance.rest_day_ot_hours = restday_overtime - HOURS_PER_DAY
                    else:
                        attendance.rest_day_hours = restday_overtime > 0 and restday_overtime or 0
                    if attendance.work_time_line_id and not (attendance.reg_holiday_ids or attendance.spl_holiday_ids):
                        if overtime_hours and restday_overtime and overtime_hours > restday_overtime:
                            overtime_hours = overtime_hours - restday_overtime
                        attendance.overtime_hours = overtime_hours > 0 and overtime_hours or 0
                    else:
                        if attendance.leave_ids and ob_leaves:
                            actual_rest_day_overtime_rendered_hours = attendance.calculate_overtime_fields_with_ob()
                            # overtime = actual_rest_day_overtime_rendered_hours
                        # if overtime > TIME_TO_RENDER:
                        #     holiday_working_hours = TIME_TO_RENDER
                        #     holiday_ot_working_hours = overtime - TIME_TO_RENDER
                        # else:
                        #     holiday_working_hours = overtime > 0 and overtime or 0
#                         if not attendance.work_time_line_id:
#                             attendance.rest_day_hours = holiday_working_hours
#                             attendance.rest_day_ot_hours = holiday_ot_working_hours
#                         if attendance.work_time_line_id:

                        if attendance.spl_holiday_ids or attendance.reg_holiday_ids:
                            reg_holiday_hours = 0
                            spl_holiday_hours = 0
                            holiday_hours = attendance.calculate_holiday_hours()
                            reg_holiday_hours += holiday_hours['regular_holiday_hours']
                            spl_holiday_hours += holiday_hours['special_holiday_hours']
                            if reg_holiday_hours > HOURS_PER_DAY:
                                reg_holiday_working_hours = HOURS_PER_DAY
                                reg_holiday_ot_working_hours = reg_holiday_hours - HOURS_PER_DAY
                            else:
                                reg_holiday_working_hours = reg_holiday_hours > 0 and reg_holiday_hours or 0
                                reg_holiday_ot_working_hours = 0

                            if spl_holiday_hours > HOURS_PER_DAY:
                                spl_holiday_working_hours = HOURS_PER_DAY
                                spl_holiday_ot_working_hours = spl_holiday_hours - HOURS_PER_DAY
                            else:
                                spl_holiday_working_hours = spl_holiday_hours > 0 and spl_holiday_hours or 0
                                spl_holiday_ot_working_hours = 0

                            attendance.is_holiday = True
                            attendance.sp_holiday_hours = attendance.spl_holiday_ids and spl_holiday_working_hours or 0
                            attendance.sp_hday_ot_hours = attendance.spl_holiday_ids and spl_holiday_ot_working_hours or 0
                            attendance.reg_holiday_hours = attendance.reg_holiday_ids and reg_holiday_working_hours or 0
                            attendance.reg_hday_ot_hours = attendance.reg_holiday_ids and reg_holiday_ot_working_hours or 0
                            # calculate overtime again with considering the holiday
                            if attendance.work_time_line_id:
                                if overtime_start_time >= holiday_end or overtime_end_time <= holiday_start:
                                    attendance.overtime_hours = overtime_hours > 0 and overtime_hours or 0
                                elif overtime_hours and not night_shift and holiday_start.date() != date_in.date():
                                    attendance.overtime_hours = overtime_hours > 0 and overtime_hours or 0
                                elif overtime_hours and (reg_holiday_hours or spl_holiday_hours) and overtime_hours > (reg_holiday_hours + spl_holiday_hours):
                                    attendance.overtime_hours = overtime_hours - (reg_holiday_hours + spl_holiday_hours)
                else:
                    attendance.overtime_hours = 0
                    if attendance.reg_holiday_ids or attendance.spl_holiday_ids:
                        attendance.is_holiday = True
            if attendance.work_time_line_id and not ob_leaves and not ((actual_time_out - actual_time_in).total_seconds() / 3600.0) > 1:
                if holiday_end and holiday_start:
                    actual_holiday_hours = (min(holiday_end, required_out) - max(holiday_start, required_in)).total_seconds() / 3600.0
                    attendance.absent_hours = (TIME_TO_RENDER - actual_holiday_hours) > 0 and (TIME_TO_RENDER - actual_holiday_hours) or 0
                else:
                    attendance.is_absent = True
                    attendance.absent_hours = TIME_TO_RENDER
            else:
                attendance.is_absent = False
            if attendance.is_absent or attendance.is_suspended or actual_time_out <= required_in or actual_time_in >= required_out:
                attendance.worked_hours = 0
            if not attendance.work_time_line_id or (not night_shift and attendance.is_holiday):
                attendance.overtime_hours = attendance.overtime_hours > 0 and attendance.overtime_hours or 0
                attendance.is_absent = False
                attendance.absent_hours = 0
                attendance.ob_hours = 0
            if holiday_end and holiday_start and required_in >= holiday_start and required_out <= holiday_end:
                attendance.worked_hours = 0
            if attendance.work_time_line_id and leaves and not ob_leaves and not attendance.is_holiday and not attendance.worked_hours:
                attendance.is_absent = True
                attendance.absent_hours = TIME_TO_RENDER - attendance.leave_hours - attendance.leave_wop_hours
            if (leave_wp_hours + leave_wop_hours + ob_hours + attendance.late_hours + attendance.undertime_hours) >= TIME_TO_RENDER:
                attendance.worked_hours = 0
            """if holidays(Holiday Settings)"""
            if attendance.check_in and attendance.check_out and schedule_week_days and (attendance.reg_holiday_ids or attendance.spl_holiday_ids):
                attendance.absent_hours = attendance._validate_holidays(schedule_week_days)
    # FOR REFRACTORING

    def calculate_leave_hours(self, leave, lunch_break, lunch_break_period,
                              date_in, date_out, required_in, required_out, worked_hours):
        leave_hours = 0
        worked_leave_hours = 0
        leave_wp_hours = 0
        leave_wop_hours = 0
#         leave = self.leave_ids.filtered(lambda l: not l.holiday_status_id.is_ob)
        date_from = (context_timestamp(self, from_string(leave.date_from))).replace(second=0)
        date_to = (context_timestamp(self, from_string(leave.date_to))).replace(second=0)
#         leave_hours = (date_out - date_in).total_seconds() / 3600.0
        if date_in >= required_out or date_out <= required_in or self.is_absent or self.is_suspended or self.is_leave:
            date_in = required_in
            date_out = required_out
        if self.work_time_line_id.break_period:
            if date_in < lunch_break:
                worked_leave_hours += (min([lunch_break, date_out]) - max(
                    [required_in, date_in])).total_seconds() / 3600.0
                leaves = (min([lunch_break, date_to, date_out]) - max(
                    [required_in, date_from, date_in])).total_seconds() / 3600.0
                if leaves > 0:
                    worked_leave_hours -= leaves

            if lunch_break_period < required_out and date_out > lunch_break_period:
                worked_leave_hours += (min([required_out, date_out]) - max(
                    [lunch_break_period, required_in, date_in])).total_seconds() / 3600.0
                leaves = (min([required_out, date_to, date_out]) - max(
                    [lunch_break_period, date_from, required_in, date_in])).total_seconds() / 3600.0
                if leaves > 0:
                    worked_leave_hours -= leaves

            if date_in > lunch_break_period and date_in < required_out:
                worked_leave_hours = (min([required_out, date_out]) - max(
                    [lunch_break_period, date_in])).total_seconds() / 3600.0

            if lunch_break <= date_in <= lunch_break_period:
                worked_leave_hours = (min([date_out, required_out]) - max(
                    [date_in, lunch_break_period])).total_seconds() / 3600.0
                leaves = (min([date_out, date_to, required_out]) - max(
                    [date_in, date_from, lunch_break_period])).total_seconds() / 3600.0
                if leaves > 0:
                    worked_leave_hours -= leaves

            if lunch_break <= date_in <= date_out <= lunch_break_period or \
                    date_from <= date_in <= date_out <= date_to:
                worked_leave_hours = 0

            if date_from == date_to or (date_to - timedelta(hours=9)) == date_from:
                if date_from < lunch_break:
                    leave_hours += (min([lunch_break, date_to]) - max(
                        [required_in, date_from])).total_seconds() / 3600.0
                if lunch_break_period < required_out and date_to > lunch_break_period:
                    leave_hours += (min([date_to, required_out]) - max(
                        [lunch_break_period, date_from, required_in])).total_seconds() / 3600.0
                if lunch_break <= date_from <= lunch_break_period:
                    leave_hours = (min([date_to, required_out]) - max(
                        [date_from, lunch_break_period])).total_seconds() / 3600.0
                if lunch_break <= date_from <= date_to <= lunch_break_period:
                    leave_hours = 0
            else:
                leaves_duration = (date_to - date_from)
                leave_days = (leaves_duration).days + float(leaves_duration.seconds) / 28800
                leave_hours = round(leave_days) * 8.0

        else:
            worked_leave_hours = (min([required_out, date_out]) - max(
                [required_in, date_in])).total_seconds() / 3600.0
            leave_hours += (min([required_out, date_to]) - max(
                [required_in, date_from])).total_seconds() / 3600.0
        # Added code - start marker
        """COMPUTE LWP in Attendances Module"""
        if leave.holiday_status_id.leave_remarks == 'wp' and not leave.holiday_status_id.is_ob:
            leave_wp_hours += leave_hours
        # Added code - end marker
        elif leave.holiday_status_id.leave_remarks == 'wop' and not leave.holiday_status_id.is_ob:
            leave_wop_hours += leave_hours

        """Regular Hours Calculation"""
        if worked_hours:
            worked_hours -= leave_wp_hours - leave_wop_hours
        else:
            worked_hours = worked_leave_hours
        return {'worked_hours': worked_hours, 'leave_wp_hours': leave_wp_hours, 'leave_wop_hours': leave_wop_hours}

    def calculate_holiday_hours(self):
        for attendance in self:
            holiday_hours = 0
            holiday_ot_hours = 0
            date_in = context_timestamp(self, from_string(attendance.check_in)).replace(second=0)
            date_out = context_timestamp(self, from_string(attendance.check_out)).replace(second=0)

            ob_leaves = attendance.leave_ids.filtered(lambda l: l.holiday_status_id.is_ob)
            overtime_start = context_timestamp(self, from_string(attendance.overtime_id.start_time)).replace(second=0)
            overtime_end = context_timestamp(self, from_string(attendance.overtime_id.end_time)).replace(second=0)
            leave_date_in = [date_in]
            leave_date_out = [date_out]
            for leave in ob_leaves:
                leave_date_in.append(context_timestamp(self, from_string(leave.date_from)).replace(second=0))
                leave_date_out.append(context_timestamp(self, from_string(leave.date_to)).replace(second=0))
            date_in = min(leave_date_in)
            date_out = max(leave_date_out)
            holidays = attendance.reg_holiday_ids + attendance.spl_holiday_ids
            regular_holiday_hours = 0
            special_holiday_hours = 0
            for holiday in holidays:
                hours = 0
                if holiday.holiday_type == 'regular':
                    holiday_start = (context_timestamp(self, from_string(holiday.holiday_start))).replace(second=0)
                    holiday_end = (context_timestamp(self, from_string(holiday.holiday_end))).replace(second=0)
                    regular_holiday_hours += (min([date_out, overtime_end, holiday_end]) - max([date_in, overtime_start, holiday_start])).total_seconds() / 3600.0
                if holiday.holiday_type == 'special':
                    holiday_start = (context_timestamp(self, from_string(holiday.holiday_start))).replace(second=0)
                    holiday_end = (context_timestamp(self, from_string(holiday.holiday_end))).replace(second=0)
                    special_holiday_hours += (min([date_out, overtime_end, holiday_end]) - max([date_in, overtime_start, holiday_start])).total_seconds() / 3600.0
            if attendance.overtime_id.with_break:
                ot_break_period = attendance.overtime_id.break_period + attendance.overtime_id.break_period2
                if attendance.reg_holiday_ids and attendance.spl_holiday_ids:
                    nextday = holidays.sorted(key=lambda l: l.holiday_start, reverse=True)[0]
                    if nextday and nextday.holiday_type == 'special':
                        special_holiday_hours = special_holiday_hours - ot_break_period
                    elif nextday and nextday.holiday_type == 'regular':
                        regular_holiday_hours = regular_holiday_hours - ot_break_period
                else:
                    special_holiday_hours = special_holiday_hours - ot_break_period
                    regular_holiday_hours = regular_holiday_hours - ot_break_period
            return {'regular_holiday_hours': regular_holiday_hours > 0 and regular_holiday_hours or 0, 'special_holiday_hours': special_holiday_hours > 0 and special_holiday_hours or 0}

    def calculate_overtime_fields_with_ob(self):
        actual_rest_day_overtime_rendered_hours = 0

        if self.leave_ids:
            overtime = 0
            ob_leaves = self.leave_ids.filtered(lambda l: l.holiday_status_id.is_ob)
            for leave in ob_leaves:
                date_in = leave.date_from
                date_out = leave.date_to
                min_end = min([date_out, self.overtime_id.end_time])
                max_start = max([date_in, self.overtime_id.start_time])
                overtime = (datetime.strptime(min_end, '%Y-%m-%d %H:%M:%S') - datetime.strptime(max_start, '%Y-%m-%d %H:%M:%S')).total_seconds() / 3600.0
                actual_rest_day_overtime_rendered = (datetime.strptime(min_end, '%Y-%m-%d %H:%M:%S') - datetime.strptime(max_start, '%Y-%m-%d %H:%M:%S'))
                actual_rest_day_overtime_rendered_hours = actual_rest_day_overtime_rendered.total_seconds() / 3600.0
        if self.overtime_id.with_break:
            break_period_within = self.overtime_id.break_period + self.overtime_id.break_period2
            if self.leave_ids:
                for leave in self.leave_ids:
                    if leave.holiday_status_id and leave.holiday_status_id.is_ob:
                        actual_rest_day_overtime_rendered_hours = overtime - break_period_within
                    else:
                        actual_rest_day_overtime_rendered_hours -= break_period_within
        return actual_rest_day_overtime_rendered_hours

    is_hide_check_time = fields.Boolean('Hide actual time', help='Hide check in/out time if both are same',
                                        compute='_compute_hide_check_time')
    schedule_in = fields.Datetime('Schedule In', compute='_compute_schedule', store=True)
    schedule_out = fields.Datetime('Schedule Out', compute='_compute_schedule', store=True)

    temp_in = fields.Datetime('Change In')
    temp_out = fields.Datetime('Change Out')

    work_time_line_id = fields.Many2one('hr.employee_schedule.work_time.lines',
                                        'Work Timeline',
                                        ondelete='restrict',
                                        help='The employee work time schedule.')

#     leave_hours = fields.Float('Leave Hours', compute='_worked_hours_computation', store=True)

#     work_time_line_id = fields.Many2one('hr.employee_schedule.work_time.lines',
#                                         'Work Timeline',
#                                         ondelete='restrict',
#                                         help='The employee work time schedule.')

    leave_hours = fields.Float('LWP Hours', compute='_worked_hours_computation', store=True)
    leave_wop_hours = fields.Float('LWOP Hours', compute='_worked_hours_computation', store=True)

    worked_hours = fields.Float(compute='_worked_hours_computation')
    absent_hours = fields.Float('Absent Hours', compute='_worked_hours_computation', store=True)
    late_hours = fields.Float('Late Hours', compute='_compute_late_hours', store=True)
    undertime_hours = fields.Float('Undertime', compute='_compute_undertime_hours', store=True)
    night_diff_hours = fields.Float('Night Differential', compute='_compute_night_diff_hours', store=True)

    sp_holiday_hours = fields.Float('Special Holiday Hours', compute="_worked_hours_computation", store=True)
    reg_holiday_hours = fields.Float('Regular Holiday Hours', compute="_worked_hours_computation", store=True)
    ob_hours = fields.Float('OB Hours', compute='_worked_hours_computation', store=True)

    overtime_hours = fields.Float('Overtime Hours', compute="_worked_hours_computation", store=True)
    rest_day_hours = fields.Float('Rest Day Hours', compute="_worked_hours_computation", store=True)
    night_diff_ot_hours = fields.Float('Night Differential Overtime', compute='_compute_night_diff_overtime_hours',
                                       store=True)
    rest_day_ot_hours = fields.Float('Rest Day Overtime')
    reg_hday_ot_hours = fields.Float('Regular Holiday', compute='_worked_hours_computation', store=True)
    sp_hday_ot_hours = fields.Float('Special Holiday', compute='_worked_hours_computation', store=True)

    request_change_id = fields.Many2one('hr.attendance.change', 'Request For Change', ondelete='restrict')
    overtime_id = fields.Many2one('hr.attendance.overtime', 'Overtime', ondelete='restrict', help="Overtime Hours")
    holiday_id = fields.Many2one('hr.attendance.holidays', 'Holiday', ondelete='restrict', help="Holiday Reference")

    leave_ids = fields.Many2many('hr.holidays', 'leave_attendance_rel', 'attendance_id', 'leave_id', 'Leaves',
                                 ondelete='restrict', help="Leaves Reference")

    spl_holiday_ids = fields.Many2many('hr.attendance.holidays', 'splhol_attendance_rel', 'attendance_id',
                                       'attendance_holiday_id', 'Special Holiday', ondelete='restrict',
                                       help="Special Holiday Reference")
    reg_holiday_ids = fields.Many2many('hr.attendance.holidays', 'reghol_attendance_rel', 'attendance_id',
                                       'attendance_holiday_id', 'Regular Holiday', ondelete='restrict',
                                       help="Regular Holiday Reference")

    rest_day_overtime = fields.Boolean('Rest Day Overtime', related="overtime_id.rest_day_overtime", store=True)
    is_absent = fields.Boolean('Absent')
    is_leave = fields.Boolean('Leaves')
    is_holiday = fields.Boolean('Holiday')
    is_ob = fields.Boolean('Official Business')
    is_raw = fields.Boolean('Raw Logs', help="If the attendance logs process from a raw file")
    is_suspended = fields.Boolean('Suspended', help="Link to disciplinary actions.")
    remarks = fields.Text('Remarks')

    @api.onchange('leave_ids', 'spl_holiday_ids', 'reg_holiday_ids')
    def onchange_boolean(self):
        for rec in self:
            if not rec.reg_holiday_ids and not rec.spl_holiday_ids:
                rec.is_holiday = False
            for leave in rec.leave_ids:
                if leave.holiday_status_id.is_ob:
                    rec.is_leave = True
            if not rec.leave_ids:
                rec.is_leave = False


    def get_date_intersection(date_ranges):
        # Convert the date strings to datetime objects
        date_ranges = [(datetime.strptime(start, '%Y-%m-%d %H:%M:%S'), 
                        datetime.strptime(end, '%Y-%m-%d %H:%M:%S')) 
                    for start, end in date_ranges]

        # Sort the date ranges by start time
        date_ranges.sort(key=lambda x: x[0])

        # Check intersection of all date ranges
        max_start = date_ranges[0][0]
        min_end = date_ranges[0][1]
        for i in range(1, len(date_ranges)):
            start_i, end_i = date_ranges[i]
            if start_i <= min_end:
                max_start = max(max_start, start_i)
                min_end = min(min_end, end_i)
            else:
                # No overlap, return None
                return None

        return max_start, min_end


    def gen_schedule_out(self):
        self.schedule_out = self.schedule_in + timedelta(hours=9)

    
    # NIGHT DIFF
    def calculate_night_hours(date_range):
        start_time, end_time = date_range
        night_start = datetime.strptime(start_time.strftime('%Y-%m-%d') + ' 22:00:00', '%Y-%m-%d %H:%M:%S')
        night_end = datetime.strptime(start_time.strftime('%Y-%m-%d') + ' 06:00:00', '%Y-%m-%d %H:%M:%S') + timedelta(days=1)
        night_hours = 0

        # Calculate the number of seconds in the timedelta object
        night_seconds = (night_end - night_start).seconds + (night_end - night_start).days * 24 * 3600

        # If the time range doesn't overlap with the night hours, return 0
        if end_time <= night_start or start_time >= night_end:
            return night_hours

        # Otherwise, calculate the number of night hours as the minimum of the night hours and the duration of the time range
        else:
            night_hours = int(min(night_seconds / 3600, (end_time - start_time).total_seconds() / 3600))
            return night_hours


class HRAttendanceChange(models.Model):
    _name = 'hr.attendance.change'
    _description = 'Request for Change of Attendance'
    _inherit = ['mail.thread']
    _order = 'date_applied desc'

    def _subscribe_manager(self, partner_id, values):
        """Allows to add the manager of the employee as followers of this record."""
        message_follower_ids = values.get('message_follower_ids') or []  # webclient can send None or False
        message_follower_ids += \
            self.env['mail.followers']._add_follower_command(self._name, [], {partner_id: None}, {}, force=True)[0]
        return message_follower_ids

    def _notify_manager(self, subject, body, follower_ids):
        """Allows to notify the manager of employee."""
        self.message_post(body=body, subject=subject, subtype='mt_comment', partner_ids=[follower_ids])

    @api.model
    def create(self, values):
        employee_id = values['employee_id']
        employee = self.env['hr.employee'].sudo().browse(employee_id)
        res = super(HRAttendanceChange, self.with_context(mail_create_nolog=True)).create(values)
        if employee.parent_id and employee.parent_id.user_id:
            partner_id = employee.parent_id.user_id.partner_id.id
            values['message_follower_ids'] = res._subscribe_manager(partner_id, values)
            if 'import_file' not in self._context:
                res._notify_manager(_(values['name']), _(values['description']), partner_id)
        return res

    @api.multi
    def is_adjustment(self):
        """Sets if an adjustment"""
        for record in self:
            domain = [
                ('employee_id', '=', record.employee_id.id),
                ('date_from', '<=', record.date_applied),
                ('date_to', '>=', record.date_applied), ('state', '=', 'done')]

            payslip = self.env['hr.payslip'].search(domain)

            domain = [
                ('employee_id', '=', record.employee_id.id),
                ('date_from', '<=', record.date_approved),
                ('date_to', '>=', record.date_approved), ('state', '=', 'done')]

            payslip |= self.env['hr.payslip'].search(domain)

            if payslip:

                record.write({'ca_adjustment': True})

    @api.multi
    def remove_from_attendance(self):
        """Remove official business reference from attendance."""
        domain = [('request_change_id', 'in', self.ids)]
        attendances = self.env['hr.attendance'].search(domain)
        attendances.write({'request_change_id': False, 'remarks': ''})

    def action_override(self):
        """Override lock actual time in and out."""
        for record in self:
            res = record.attendance_line.filtered(lambda r: r.is_absent)
            res.write({'is_absent': False})

    @api.multi
    def btn_allow_override(self):
        self.action_override()

    def action_draft(self):
        for record in self:
            if self.env.uid == record.employee_id.user_id.id:
                raise ValidationError(_('Unable to reset to draft own change request!'))
            self.remove_from_attendance()
        return self.write({'state': 'draft'})

    def action_pending(self):
        self.write({'state': 'pending'})

    @api.multi
    def action_approved(self):
        self.action_approve()

    @api.multi
    def action_approve(self):
        if not (self.env.user.has_group('hris.group_approver') or self.env.user.has_group('hris.group_hr_user')):
            raise UserError(_('Only an Approver can approve change of attendance requests.'))

        if self.filtered(lambda r: r.state == 'approved'):
            raise ValidationError(_('Unable to approve already approved change of attendance requests.'))

        self._check_lockout_period()
        for record in self:
            if self.env.uid != 1 and self.env.uid == record.employee_id.user_id.id:
                raise ValidationError(_('Unable to approve own change request!'))

            if record.attendance_line:

                attendances = self.env['hr.attendance']
                for attendance in record.attendance_line:
                    attendances |= attendance

                    attendance.write({'check_in': attendance.temp_in, 'check_out': attendance.temp_out})

                attendances.write({'request_change_id': record.id, 'is_absent': False, 'remarks': 'CA'})
                attendances.recompute_attendance()

        return self.write({'state': 'approved', 'date_approved': fields.Datetime.now()})

    @api.multi
    def btn_make_draft(self):
        self.action_draft()

    @api.multi
    def btn_pending(self):
        self.action_pending()

    @api.multi
    def btn_approved(self):
        self.action_approve()

    def unlink(self):
        for record in self.filtered(lambda r: r.state == 'approved'):
            raise ValidationError(_('Unable to delete record which is in approved state!'))
        return super(HRAttendanceChange, self).unlink()

    @api.constrains('date_applied')
    def _check_lockout_period(self):
        for attendance in self:

            if not attendance.ob_lockout:
                continue

            today = fields.Date.today()
            date_from = fields.Date.to_string(fields.Date.from_string(attendance.date_applied))

            domain = [
                ('start_date', '<=', today),
                ('end_date', '>=', date_from)
            ]

            cutoffs = self.env['hr.payroll.period_line'].search_count(domain)

            if cutoffs > attendance.ob_lockout_period:
                raise ValidationError(
                    _('Unable to file or process change of attendance.The lockout period has been reached!'))

    name = fields.Char('Subject', size=64, track_visibility='onchange', default="Change of Attendance", required=True)
    date_applied = fields.Date('Date Applied', default=lambda s: fields.Date.context_today(s), track_visibility='onchange',
                               help="Date Applied")
    date_approved = fields.Datetime('Date Approved', track_visibility='onchange', help="Date Approved")
    employee_id = fields.Many2one('hr.employee',
                                  'Employee',
                                  required=True,
                                  track_visibility='onchange',
                                  default=lambda self: self.env.user)

    ca_adjustment = fields.Boolean('CA Adjustment')
    attendance_line = fields.Many2many('hr.attendance', 'request_attendance_rel', 'attendance_id',
                                       'attendance_change_id')

    description = fields.Text('Description', required=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('pending', 'Pending'),
        ('approved', 'Approved')], 'Approved',
        track_visibility='onchange',
        default='draft')

    ob_lockout = fields.Boolean('Lockout', help="Enables locking out period of official business.")
    ob_lockout_period = fields.Float('Lockout Period', help="Lock out period(e.g. 2 payroll cut-offs)")


class HRAttendanceHoliday(models.Model):
    _name = 'hr.attendance.holidays'
    _description = 'HR Holidays'

    def set_attendance_holidays(self):
        """Sets holiday reference."""
        # remove holiday from attendance if the date is changed
        attendance_ids = self.env['hr.attendance'].search(['|', ('spl_holiday_ids', 'in', self.ids), ('reg_holiday_ids', 'in', self.ids)])
        if attendance_ids:
            attendance_ids.write({
                'reg_holiday_ids': [(6, 0, [])],
                'spl_holiday_ids': [(6, 0, [])],
            })
        domain_start = [('check_in', '>=', self.holiday_start),
                        ('check_in', '<=', self.holiday_end)]
        attendance_start = self.env['hr.attendance'].search(domain_start)

        domain_end = [('check_out', '>=', self.holiday_start),
                      ('check_out', '<=', self.holiday_end)]

        attendance_end = self.env['hr.attendance'].search(domain_end)

        attendance_start |= attendance_end

        if self.work_location_id:
            results = attendance_start.filtered(lambda r: r.employee_id.work_location_id
                                                and r.employee_id.work_location_id.id ==
                                                self.work_location_id.id)

            self.update_attendance_holidays(results)
        else:
            self.update_attendance_holidays(attendance_start)

    def update_attendance_holidays(self, attendances):
        """Delete and update record of holidays field."""
        if self.holiday_type == 'regular':
            attendances.write({'reg_holiday_ids': [(4, self.id)]})
            attendances.write({'spl_holiday_ids': [(3, self.id)]})

        elif self.holiday_type == 'special':
            attendances.write({'spl_holiday_ids': [(4, self.id)]})
            attendances.write({'reg_holiday_ids': [(3, self.id)]})

    @api.constrains('holiday_start', 'holiday_end')
    def _check_time(self):
        #         holiday_ids = self.env['hr.attendance.holidays'].search([])
        for record in self:
            if record.holiday_start and record.holiday_end:
                if record.holiday_end < record.holiday_start:
                    raise ValidationError(_("End of Holiday must be greater than Start of Holiday!"))
#             holiday_id = holiday_ids[:-1].filtered(lambda h: (record.holiday_start >= h.holiday_start and record.holiday_start <= h.holiday_end)
#                                                    or (record.holiday_end >= h.holiday_start and record.holiday_end <= h.holiday_end)
#                                                    and not (record.holiday_start >= h.holiday_end and record.holiday_end >= record.holiday_start)
#                                                 )
#             if holiday_id:
#                 raise ValidationError(_('You cannot create multiple holiday for one day!!!'))

    @api.model
    def create(self, vals):
        res = super(HRAttendanceHoliday, self).create(vals)
        res.set_attendance_holidays()
        return res

    @api.multi
    def write(self, vals):
        res = super(HRAttendanceHoliday, self).write(vals)
        self.set_attendance_holidays()
        return res

    @api.onchange('holiday_start')
    def onchange_holiday_start(self):

        if self.holiday_start:
            self.holiday_end = fields.Datetime.to_string(
                fields.Datetime.from_string(self.holiday_start) + timedelta(days=1))

    name = fields.Char('Holiday Name', size=32, required=True)
    holiday_start = fields.Datetime('Start of Holiday', required=True)
    holiday_end = fields.Datetime('End of Holiday', required=True)
    holiday_type = fields.Selection([('regular', 'Regular Holidays'),
                                     ('special', 'Special Non-working')], 'Holiday Type',
                                    required=True)
    recurring_holiday = fields.Boolean("Is Recurring?")
    work_location_id = fields.Many2one('hr.employee.work_location', 'Work Location')

    @api.constrains('holiday_start', 'holiday_end')
    def check_duplicate_holiday(self):
        for rec in self:
            holidays = self.env['hr.attendance.holidays'].search_count([
                ('holiday_start', '=', rec.holiday_start),
                ('holiday_end', '=', rec.holiday_end),
                ('holiday_type', '=', rec.holiday_type)
            ])
            if holidays > 1:
                raise ValidationError(_("Duplicate holidays not allowed!!"))

    def cron_recurring_holidays(self):
        holiday_obj = self.env['hr.attendance.holidays']
        recurring_holidays = holiday_obj.search([('recurring_holiday', '=', True)])
        for holiday in recurring_holidays:
            next_start_date = datetime.strptime(holiday.holiday_start, '%Y-%m-%d %H:%M:%S')
            next_end_date = datetime.strptime(holiday.holiday_end, '%Y-%m-%d %H:%M:%S')
            holiday_obj.create({
                'name': holiday.name,
                'holiday_start': next_start_date.replace(year=datetime.today().year),
                'holiday_end': next_end_date.replace(year=datetime.today().year),
                'holiday_type': holiday.holiday_type,
                'recurring_holiday': holiday.recurring_holiday,
                'work_location_id': holiday.work_location_id or False,
            })


class HRAttendanceOvertime(models.Model):
    _name = 'hr.attendance.overtime'
    _description = 'HR Attendance Overtime'
    _inherit = ['mail.thread']
    _order = 'create_date desc'

    overtime_reason = fields.Text('Reason')

    @api.constrains('employee_id', 'holiday_status_id', 'start_time', 'end_time', 'overtime_reason', 'date_approved')
    def validate_edit(self):
        context = self._context
        current_uid = context.get('uid')
        user = self.env['res.users'].browse(current_uid)
        approver = self.env['res.users'].has_group('hris.group_approver')
        first_level = self.env['res.users'].has_group('hris.group_firstlevel')
        current_user = self.env.user.groups_id.mapped('id')
        if not context.get('is_approve'):
            for record in self:
                emp = self.env['hr.employee'].search([('user_id', '=', user.id)], limit=1)
                if record.employee_id.parent_id.id == emp.id and record.employee_id.id != emp.id:
                    if user.id != 1 and first_level in current_user:
                        raise ValidationError("Unable to edit/create others record")

    def _subscribe_manager(self, partner_id, values):
        """Allows to add the manager of the employee as followers of this record."""
        message_follower_ids = values.get('message_follower_ids') or []  # webclient can send None or False
        message_follower_ids += \
            self.env['mail.followers']._add_follower_command(self._name, [], {partner_id: None}, {}, force=True)[0]

        return message_follower_ids

    def _notify_manager(self, subject, body, follower_ids):
        """Allows to notify the manager of the employee."""

        self.message_post(body=body, subject=subject, subtype='mt_comment', partner_ids=[follower_ids])

    @api.model
    def create(self, values):
        employee_id = values['employee_id']
        employee = self.env['hr.employee'].sudo().browse(employee_id)
        res = super(HRAttendanceOvertime, self.with_context(mail_create_nolog=True)).create(values)

        if employee.parent_id and employee.parent_id.user_id:
            partner_id = employee.parent_id.user_id.partner_id.id
            values['message_follower_ids'] = res._subscribe_manager(partner_id, values)

            if 'import_file' not in self._context:
                res._notify_manager(_('Request for Overtime'), _('Request for Overtime has been created'), partner_id)

        return res

    def unlink(self):
        for record in self.filtered(lambda r: r.state == 'approved'):
            raise ValidationError(_('Unable to delete record which is in approved state!'))
        return super(HRAttendanceOvertime, self).unlink()

    @api.multi
    def name_get(self):
        res = []
        for record in self:
            start_time = fields.Datetime.context_timestamp(record, fields.Datetime.from_string(record.start_time))
            end_time = fields.Datetime.context_timestamp(record, fields.Datetime.from_string(record.end_time))

            start_time = start_time.strftime('%m/%d/%Y %I:%M %p')
            end_time = end_time.strftime('%m/%d/%Y %I:%M %p')

            name = '{} {}-{}'.format(record.employee_id.name, start_time, end_time)
            res.append((record.id, name))
        return res

    """SET OVERTIME TO DRAFT"""
    def action_draft(self):
        if self.holiday_id and self.offset:
            self.holiday_id.action_refuse()
            self.holiday_id.action_draft()
        return self.write({'state': 'draft'})

    @api.constrains('start_time', 'end_time')
    def _check_date(self):
        for overtime in self:
            domain = [
                ('start_time', '<=', overtime.end_time),
                ('end_time', '>=', overtime.start_time),
                ('employee_id', '=', overtime.employee_id.id),
                ('id', '!=', overtime.id)
            ]
            count = self.search_count(domain)
            if count:
                raise ValidationError(_('You can not have 2 overtime that overlaps on same day!'))

    def cron_convert_leaves(self):
        """Automatically convert overtime with type of cdo into leaves."""
        domain = [
            ('holiday_status_id.is_cdo', '=', True),
            ('offset', '=', True),
            ('state', '=', 'draft')]

        overtime = self.search(domain)
        overtime.action_approved()
        overtime.action_make_leave()

    def get_overtime_hours(self, record):
        """Return the rendered hours"""
        attendance = self.env['hr.attendance'].search([('overtime_id', '=', record.id)])
        # if record.holiday_id and record.offset:
        #    attendance._compute_overtime_hours()

        overtime_hours = record.rest_day_overtime and attendance.mapped('rest_day_ot_hours') + attendance.mapped('rest_day_hours') \
            or attendance.mapped('overtime_hours')
        return sum(overtime_hours)
        #hours_rendered = record.hours_requested / 8.0
        # return hours_rendered

    def action_make_leave(self):
        """Create leaves."""
        for record in self:

            # Check if the current user is trying to convert their own overtime to leave
            if self.env.uid == record.employee_id.user_id.id:
                raise ValidationError(_('Unable to convert own overtime to leave!'))

            # Check if the record has offset
            if record.offset:
                # Check if the leave type is specified
                if not record.holiday_status_id:
                    raise ValidationError(_('Please specify the leave type to be created'))
                
                # Check entitlement for cumulative day off
                if (
                    record.holiday_status_id.is_cdo
                    and record.employee_id
                    and record.employee_id.contract_id
                    and record.employee_id.contract_id.job_id
                    and record.employee_id.job_id not in record.holiday_status_id.job_ids
                ):
                   raise ValidationError(_('This employee is not entitled for cumulative day off!.'))
                # Check if hours rendered is less than or equal to zero
                if self.hours_requested < 0:
                    raise ValidationError(_('Hours rendered of this overtime is less than or equal to zero!'))
                
                # If the employee is entitled for cumulative day off, calculate hours rendered
                if record.holiday_status_id.is_cdo:
                    self.hours_requested = self.get_overtime_hours(record)
                
                # Calculate the number of days for leave
                amount = record.holiday_id._get_number_of_days(record.start_time, record.end_time, record.employee_id.id)

                # If holiday_id exists, confirm and validate
                if record.holiday_id:
                    record.holiday_id.number_of_days_temp = amount
                    record.holiday_id.action_confirm()
                    record.holiday_id.action_validate()
                else:
                    # If holiday_id does not exist, create leaves and update holiday_id
                    holiday_id = self.create_leaves(record, amount)
                    return self.write({'holiday_id': holiday_id.id, 'state': 'converted'})        

        return True

    @api.multi
    def remove_from_attendance(self):
        """Remove overtime reference from attendance."""
        domain = [('overtime_id', 'in', self.ids)]
        attendances = self.env['hr.attendance'].search(domain)
        for att in attendances:
            vals = {}
            if att.rest_day_overtime:
                vals['remarks'] = ''
                vals['rest_day_ot_hours'] = ''
            vals['overtime_id'] = False
            att.write(vals)

    @api.multi
    def is_adjustment(self):
        """Sets if an adjustment"""
        for record in self:
            domain = [
                ('employee_id', '=', record.employee_id.id),
                ('date_from', '<=', record.start_time),
                ('date_to', '>=', record.start_time), ('state', '=', 'done')]

            payslip = self.env['hr.payslip'].search(domain)

            domain = [
                ('employee_id', '=', record.employee_id.id),
                ('date_from', '<=', record.date_approved),
                ('date_to', '>=', record.date_approved), ('state', '=', 'done')]

            payslip |= self.env['hr.payslip'].search(domain)

            if payslip:

                record.write({'overtime_adjustment': True})

    """"APPROVED OVERTIME"""
    @api.multi
    def action_approved(self):
        if not (self.env.user.has_group('hris.group_approver') or self.env.user.has_group('hris.group_hr_user') or self.env.user.has_group('hris.payroll_admin')):
            raise UserError(_('Only an Approver can approve overtime requests.'))

        if self.filtered(lambda r: r.state != 'draft'):
            raise ValidationError(_('Unable to approve overtime not in draft.'))

        self.check_no_ot_late()
        self._check_lockout_period()
        for record in self:

            if self.env.uid != 1 and self.env.uid == record.employee_id.user_id.id:
                raise ValidationError(_('Unable to approve own overtime!'))

            if not record.date_approved:
                record.date_approved = fields.Datetime.now()

            domain = [('employee_id', '=', record.employee_id.id),
                      ('check_out', '>=', record.start_time),
                      ('check_in', '<=', record.start_time)]

            # Search attendance if the check out is greater than or match the start period overtime 
            # and check if the check in less than or mactch the start period overtime
            attendance = self.env['hr.attendance'].search(domain, limit=1)

            # If the attendance didn't search any results
            if not attendance:
                domain = [('employee_id', '=', record.employee_id.id),
                          ('schedule_out', '>=', record.start_time),
                          ('schedule_in', '<=', record.start_time)
                          ]

                # Store the variable if there is no results
                attendance = self.env['hr.attendance'].search(domain, limit=1)

            # If the attendance didn't search any results
            if not attendance:
                domain = [('employee_id', '=', record.employee_id.id),
                          ('check_in', '<=', record.end_time),
                          ('check_out', '>=', record.start_time)
                          ]

                # Store the variable if there is no results
                attendance = self.env['hr.attendance'].search(domain, limit=1)

            attendance.write({'overtime_id': record and record.id or False})

            # If the attendance can't get the overtime id 
            # This means that overtime can be approved if it starts immediately after the employee's check-out time. 
            if attendance and not attendance.work_time_line_id and not attendance.holiday_id:
                record.rest_day_overtime = True

            if attendance.work_time_line_id:
                record.rest_day_overtime = False

            # if record.rest_day_overtime:
                
            #     attendance.write({
            #         'rest_day_overtime': True,
            #         'remarks': 'RD'
            #     })
            record.is_adjustment()

        return self.write({'state': 'approved'})

    @api.multi
    def check_holiday(self):
        """Set if overtime is a holiday"""

        holiday = self.env['hr.attendance.holidays'].search([('holiday_start', '<=', self.start_time),
                                                             ('holiday_end', '>=', self.end_time)], limit=1)
        holiday |= self.env['hr.attendance.holidays'].search([('holiday_start', '>=', self.end_time),
                                                              ('holiday_end', '<=', self.start_time)], limit=1)

        holiday |= self.env['hr.attendance.holidays'].search([('holiday_start', '>=', self.start_time),
                                                              ('holiday_start', '<=', self.end_time)], limit=1)

        holiday |= self.env['hr.attendance.holidays'].search([('holiday_end', '>=', self.start_time),
                                                              ('holiday_end', '<=', self.end_time)], limit=1)

        for record in holiday:
            if record.holiday_type == 'regular':
                self.legal_holiday = True
            else:
                self.legal_holiday = False

            if record.holiday_type == 'special':
                self.special_holiday = True
            else:
                self.special_holiday = False

    @api.onchange('start_time', 'end_time')
    def onchange_start_end(self):
        if self.start_time and self.end_time:
            self.check_holiday()

    def create_leaves(self, record, amount):
        """Generate leaves for specific employee depending each process type."""
        holidays = self.env['hr.holidays']
        values = {}
        values['holiday_status_id'] = record.holiday_status_id and record.holiday_status_id.id
        values['name'] = record.holiday_status_id and record.holiday_status_id.name + "(Offset)"
        values['type'] = 'add'
        values['year'] = fields.Datetime.from_string(record.end_time).year
        values['holiday_type'] = 'employee'
        values['category_id'] = False
        values['employee_id'] = record.employee_id and record.employee_id.id
        values['number_of_days_temp'] = amount
        leaves = holidays.create(values)
        leaves.action_validate()

        return leaves

    def action_disapproved(self):
        if not (self.env.user.has_group('hris.group_approver') or self.env.user.has_group('hris.group_hr_user') or self.env.user.has_group('hris.payroll_admin')):
            raise UserError(_('Only an Approver can approve overtime requests.'))

        if self.filtered(lambda r: r.state != 'draft'):
            raise ValidationError(_('Unable to disapprove overtime not in draft.'))

        for record in self:
            if self.env.uid == record.employee_id.user_id.id:
                raise ValidationError(_('Unable to disapprove own overtime!'))

            if record.holiday_id:
                record.holiday_id.action_refuse()
        return self.write({'state': 'disapproved'})

    def action_cancelled(self):
        if self.holiday_id:
            self.holiday_id.action_refuse()
        return self.write({'state': 'cancelled'})

    @api.multi
    def btn_make_draft(self):
        self.action_draft()

    @api.multi
    def btn_approved(self):
        self.action_approved()

    @api.multi
    def btn_make_leave(self):
        self.action_make_leave()

    @api.multi
    def btn_cancelled(self):
        self.action_cancelled()

    @api.multi
    def btn_disapproved(self):
        self.action_disapproved()

    @api.constrains('start_time', 'end_time')
    def _check_validity_start_end_time(self):
        """ verifies if start time is earlier than end time. """
        for overtime in self:
            if overtime.start_time and overtime.end_time:
                if overtime.end_time < overtime.start_time:
                    raise ValidationError(_('"End time" time cannot be earlier than "Start time" time.'))

    @api.constrains('hours_requested', 'start_time', 'end_time')
    def check_hours_requested(self):
        minimum_hours = float(self.env['ir.config_parameter'].search([('key', '=', 'minimum.overtime.hours')], limit=1).value)
        msg = 'Overtime rendered must be greater than or equal to %s hour(s)!' % minimum_hours
        for record in self:
            if record.hours_requested < minimum_hours:
                raise ValidationError(_(msg))

    @api.constrains('start_time', 'end_time')
    def check_no_ot_late(self):
        for record in self:
            if record.no_ot_late:
                domain = [('employee_id', '=', record.employee_id.id),
                          ('check_out', '>=', record.start_time),
                          ('check_in', '<=', record.start_time)]

                attendance = self.env['hr.attendance'].search(domain, limit=1)
                if attendance and attendance.work_time_line_id and attendance.late_hours > 0:
                    raise ValidationError(_('Unable to file your requested overtime.\nYou have tardiness today.'))

    @api.depends('start_time', 'end_time', 'with_break', 'break_period', 'break_period2')
    def _compute_hours_requested(self):
        for overtime in self:
            if overtime.start_time and overtime.end_time:
                date_in = fields.Datetime.context_timestamp(self, fields.Datetime.from_string(overtime.start_time))
                date_out = fields.Datetime.context_timestamp(self, fields.Datetime.from_string(overtime.end_time))

                hours_requested = (date_out - date_in)
                total_hours_requested = hours_requested.total_seconds() / 3600.0

                if overtime.with_break:
                    hour, minute = float_time_convert(overtime.break_period)
                    hour1, minute1 = float_time_convert(overtime.break_period2)

                    break_time = hours_requested - timedelta(hours=hour, minutes=minute, seconds=0) - timedelta(hours=hour1, minutes=minute1, seconds=0)
                    total_hours_requested = break_time.total_seconds() / 3600.0

                    if total_hours_requested < 8 and overtime.break_period2 > 0:
                        raise ValidationError(_('Hours requested is lesser than 8 hours!'))

                    if total_hours_requested < 0:
                        raise ValidationError(_('Hours requested is lesser than break period!'))

                overtime.hours_requested = total_hours_requested

    @api.constrains('start_time')
    def _check_lockout_period(self):
        for overtime in self:

            if not overtime.ot_lockout:
                continue

            today = fields.Date.today()
            date_from = fields.Date.to_string(
                fields.Datetime.context_timestamp(self, fields.Datetime.from_string(overtime.start_time)))

            domain = [
                ('start_date', '<=', today),
                ('end_date', '>=', date_from)
            ]

            cutoffs = self.env['hr.payroll.period_line'].search_count(domain)

            if cutoffs > overtime.ot_lockout_period:
                raise ValidationError(_('Unable to file or process overtime.The lockout period has been reached!'))

    employee_id = fields.Many2one('hr.employee',
                                  'Employee',
                                  required=True,
                                  track_visibility='onchange')

    start_time = fields.Datetime('Start time',
                                 required=True,
                                 track_visibility='onchange')

    end_time = fields.Datetime('End time',
                               required=True,
                               track_visibility='onchange')

    date_approved = fields.Datetime('Date Approved')
    offset = fields.Boolean('Offset', help="If it will be converted to leaves.")
    holiday_status_id = fields.Many2one('hr.holidays.status', 'Leave Type')
    holiday_id = fields.Many2one('hr.holidays', 'Leaves')

    with_break = fields.Boolean('With Break')

    break_period = fields.Float('Break Period within 8 hours')
    break_period2 = fields.Float('Break Period(In excess of 8 hours)')

    hours_requested = fields.Float('Hours Requested',
                                   compute="_compute_hours_requested",
                                   track_visibility='onchange')

    rest_day_overtime = fields.Boolean('Rest Day Overtime')
    legal_holiday = fields.Boolean('Legal Holiday')
    special_holiday = fields.Boolean('Special Holiday')
    overtime_adjustment = fields.Boolean('Overtime Adjustment', help="An overtime adjustment")
    ot_lockout = fields.Boolean('Lockout', help="Enables locking out period of overtime.")
    ot_lockout_period = fields.Float('Lockout Period', help="Lock out period(e.g. 2 payroll cut-offs)")
    no_ot_late = fields.Boolean('No Overtime', help="Employees with tardiness on the same day of requested overtime shall be void.")
    state = fields.Selection([('draft', 'Draft'),
                              ('approved', 'Approved'),
                              ('disapproved', 'Disapproved'),
                              ('cancelled', 'Cancelled'),
                              ('converted', 'Converted')],
                             track_visibility='onchange',
                             default="draft",
                             help="* Draft: Newly create record.\n"
                                  "* Approved: The request for overtime is approved.\n"
                                  "* Disapproved: The request for overtime is disapproved.\n"
                                  "* Cancelled: The record was cancelled.")
# start


class HRAttendanceRequest(models.Model):
    _name = 'hr.attendance.request'
    _description = 'HR Attendance Request'
    _rec_name = 'employee_id'
    _order = 'id DESC'

    @api.multi
    def request_approve(self):
        for item in self:
            item.action_approve()

    @api.model
    def create(self, vals):
        res = super(HRAttendanceRequest, self).create(vals)
        res.prepare_payslip()
        res.write({'state': 'approve'})
        return res

    @api.model
    def fields_view_get(self, view_id=None, view_type='form', toolbar=False, submenu=False):
        res = super(HRAttendanceRequest, self).fields_view_get(view_id=view_id, view_type=view_type, toolbar=toolbar, submenu=False,)
        firstlevel = self.env['res.users'].has_group('hris.group_firstlevel')
        approver = self.env['res.users'].has_group('hris.group_approver')
        time_keeper = self.env['res.users'].has_group('hris.group_timekeeper')
        current_user = self.env.user.groups_id.mapped('id')
        doc = etree.XML(res['arch'])

        if view_type == 'tree':
            if time_keeper in current_user:
                for node in doc.xpath("//tree"):
                    node.set('create', '0')
                    node.set('delete', '0')
                    node.set('edit', '0')
        if view_type == 'form':
            if time_keeper in current_user:
                for node in doc.xpath("//form"):
                    node.set('create', '0')
                    node.set('delete', '0')
                    node.set('edit', '0')

        if view_type == 'form':

            if firstlevel or approver or time_keeper in current_user:
                domain = "['|',('user_id','=',uid),('parent_id.user_id','=',uid)]"
                emp_id = doc.xpath("//field[@name = 'employee_id']")
                for node in emp_id:
                    node.set('domain', domain)
                    setup_modifiers(node, res['fields']['employee_id'])

        res['arch'] = etree.tostring(doc)
        return res

    def prepare_payslip(self):
        """Create or update payslips."""
        for record in self:
            res = self.env['hr.attendance.request_line'].\
                read_group([('attendance_request_id', '=', record.id)], ['code', 'number_of_hours'], ['code'])

            data = dict((record['code'], record['number_of_hours']) for record in res)
            ttyme = datetime.fromtimestamp(time.mktime(time.strptime(record.payroll_period_id.start_date, "%Y-%m-%d")))
            locale = self.env.context.get('lang') or 'en_US'

            values = {}
            values['employee_id'] = record.employee_id.id
            values['contract_id'] = record.employee_id.contract_id.id
            values['struct_id'] = record.employee_id.contract_id.struct_id.id

            values['payroll_period_id'] = record.payroll_period_id.id
            values['date_from'] = record.payroll_period_id.start_date
            values['date_to'] = record.payroll_period_id.end_date
            values['date_release'] = record.payroll_period_id.date_release
            values['name'] = _('Salary Slip of %s for %s') % (record.employee_id.name, tools.ustr(babel.dates.format_date(date=ttyme, format='MMMM-y', locale=locale)))

            worked_days = []
            counter = 6

            worked_days_list = []
            codes = self.env['hr.attendance.request_line'].get_codes()

            for code in codes:
                counter += 1

                worked_hours_values = {
                    'name': codes[code],
                    'code': code,
                    'sequence': counter,
                    'number_of_hours': 0,
                    'number_of_days': 0,
                    'contract_id': record.employee_id.contract_id.id
                }
                worked_days_list.append(worked_hours_values)

            for r in data:
                code_name = self.env['hr.attendance.request_line'].get_code_name(r)
                white_list = filter(lambda r: r['name'] == code_name, worked_days_list)
                worked_days_list = filter(lambda r: r['name'] != code_name, worked_days_list)

                if white_list:
                    counter = white_list[0]['sequence']
                worked_days.append((0, 0, {
                                    'name': code_name,
                                    'code': r,
                                    'sequence': counter,
                                    'number_of_hours': data[r],
                                    'number_of_days': data[r] / 8.0,
                                    'contract_id': record.employee_id.contract_id.id
                                    }))

            # we added all codes
            values['worked_days_line_ids'] = worked_days + [(0, 0, v) for v in worked_days_list]
            # Remove current worked days and write new
            if record.payslip_id:
                record.payslip_id.worked_days_line_ids.unlink()
                record.payslip_id.write(values)
                # record.payslip_id.action_compute_attendance()

            else:
                record.payslip_id = self.env['hr.payslip'].create(values).id

            if record.payslip_id:
                record.payslip_id.compute_sheet()
                # record.payslip_id.action_compute_attendance()

    @api.constrains('request_line')
    def check_attendance_request_validity(self):
        """Check request attendance date validity."""
        for record in self:
            if record.request_line.filtered(lambda r: r.attendance_date < record.payroll_period_id.start_date
                                            or r.attendance_date > record.payroll_period_id.end_date):
                raise ValidationError(_('Request attendance date is not covered by payroll period'))

    @api.multi
    def unlink(self):
        for record in self.filtered(lambda r: r.state != 'draft'):
            raise UserError(_('Unable to delete attendance request not in draft!.'))
        return super(HRAttendanceRequest, self).unlink()

    def action_draft(self):
        """Resets to draft."""
        return self.write({'state': 'draft'})

    def action_approve(self):
        """Create and update the requests."""
        self.prepare_payslip()
        return self.write({'state': 'approve'})

    def action_disapprove(self):
        """Disapprove the requests."""
        return self.write({'state': 'disapprove'})

    def btn_approve(self):
        self.action_approve()

    def btn_disapprove(self):
        self.action_disapprove()

    def btn_reset_to_draft(self):
        self.action_draft()

    payroll_period_id = fields.Many2one('hr.payroll.period_line', 'Payroll Period', required=True)
    payslip_id = fields.Many2one('hr.payslip', 'Payroll')
    employee_id = fields.Many2one('hr.employee', 'Employee', required=True)
    request_line = fields.One2many('hr.attendance.request_line', 'attendance_request_id', 'Attendance Request')
    state = fields.Selection([('draft', 'Draft'),
                              ('approve', 'Approved'),
                              ('disapprove', 'Disapproved')], 'State', default='draft')
    remarks = fields.Text('Remarks')


class HRAttendanceRequestLine(models.Model):
    _name = 'hr.attendance.request_line'

    def get_codes(self):
        """Returns list of codes."""
        data = OrderedDict(self.fields_get(allfields=['code'])['code']['selection'])
        return data

    def get_code_name(self, key=''):
        """Returns code value."""
        if not key:
            return ''
        return self.get_codes()[key]

    attendance_request_id = fields.Many2one('hr.attendance.request', 'Request')
    code = fields.Selection([
        ('RegWrk', 'Regular Work'),
        ('RestDayOTNtdff', 'Rest Day OT Night Diff'),
        ('NightShiftDiff', 'Night Shift Differential'),
        ('SpNd', 'Special Holiday Night Differential'),
        ('ABS', 'Absences'),
        ('UT', 'Undertime'),
        ('TD', 'Tardiness'),
        ('RestDayWrk', 'Rest Day Work'),
        ('SpeHolWrk', 'Special Holiday Work'),
        ('RegHolWrk', 'Regular Holiday Work'),
        ('RegOT', 'Regular Overtime'),
        ('NightDiff', 'Night Differential'),
        ('RestOT', 'Rest Day Overtime'),
        ('SpeHolOT', 'Special Holiday Overtime'),
        ('RegHolOT', 'Regular Holiday Overtime')
    ], 'Code', required=True)
    number_of_hours = fields.Float('Number of Hours', required=True)
    attendance_date = fields.Date('Attendance Date')

# end


class HRPayrollAttendance(models.Model):
    _inherit = 'hr.payslip'
    _order = 'date_release DESC'

    late_attendances = fields.Many2many('hr.attendance')

    def get_year_to_date(self, employee_id, date_from=None, date_to=None):
        """Returns the total year to date of each salary rule."""
        if date_from is None:
            date_from = fields.Date.from_string(date_to)
        if date_from:
            date_from = fields.Date.from_string(date_to).replace(month=1, day=1).strftime('%Y-%m-%d')

        domain = [
            ('slip_id.employee_id', '=', employee_id),
            ('slip_id.date_release', '>=', date_from),
            ('slip_id.date_release', '<=', date_to),
            ('slip_id.state', '=', 'done'),
            ('slip_id.credit_note', '=', False)
        ]

        results = self.env['hr.payslip.line'].read_group(domain, ['salary_rule_id', 'total'], ['salary_rule_id'])
        ytd = dict((data['salary_rule_id'][0], data['total']) for data in results)
        return ytd

    @api.constrains('date_from', 'date_to')
    def check_payroll_period_payslip(self):
        for rec in self:
            payslips = self.env['hr.payslip'].search([('date_from', '>=', rec.date_from),
                                                      ('date_to', '<=', rec.date_to),
                                                      ('employee_id', '=', rec.employee_id.id),
                                                      ('id', '!=', rec.id),
                                                      '|', ('credit_note', '=', True),
                                                      ('credit_note', '=', False)])
            refund_payslip = payslips.filtered(lambda l: l.credit_note)
            payslip = payslips.filtered(lambda l: l.credit_note == False)
            if len(refund_payslip) > 1 or len(payslip) > 1:
                raise ValidationError(_("Employee %s already had a Payroll processed for the selected Payroll Period!!") % rec.employee_id.name)

    def remove_payslip_reference(self):
        for rec in self:
            holidays = self.env['hr.holidays'].search([('hr_payslip_id', '=', rec.id)])
            if holidays:
                holidays.write({'hr_payslip_id': False})

    @api.multi
    def action_payslip_cancel(self):
        res = super(HRPayrollAttendance, self).action_payslip_cancel()
        self.remove_payslip_reference()
        return res

    @api.multi
    def refund_sheet(self):
        res = super(HRPayrollAttendance, self).refund_sheet()
        self.remove_payslip_reference()
        return res


    def get_worked_hour_lines(self, employee_id, date_from, date_to):
        """Returns employee attendances timesheet."""
        date_f = fields.Datetime.from_string(date_from)
        date_t = fields.Datetime.from_string(date_to)

        start_dt = date_f.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = date_t.replace(hour=7, minute=0, second=59, microsecond=999999) + timedelta(days=1)

        timezone = self.env.user.tz or self._context.get('tz')

        date_from = fields.Datetime.to_string(context_utc(start_dt, timezone))
        date_to = fields.Datetime.to_string(context_utc(end_dt, timezone))

        domain = [('employee_id', '=', employee_id),
                  ('check_in', '>=', date_from),
                  ('check_out', '<=', date_to),
                  ('request_change_id', '=', False)]
        
        att = self.env['hr.attendance'].search(domain)

        domain = [('employee_id', '=', employee_id),
                  ('check_in', '>=', date_from),
                  ('check_out', '<=', date_to),
                  ('request_change_id', '!=', False),
                  ('request_change_id.state', '=', 'approved'),
                  ('request_change_id.date_approved', '>=', date_from),
                  ('request_change_id.date_approved', '<=', date_to),
                  ]

        att2 = self.env['hr.attendance'].search(domain)
        att |= att2
        domain
        domain = [('employee_id', '=', employee_id),
                  ('check_in', '>=', date_from),
                  ('check_out', '<=', date_to),
                  ('request_change_id', '!=', False),
                  ('request_change_id.state', '=', 'approved'),
                  ('request_change_id.date_applied', '>=', date_from),
                  ('request_change_id.date_applied', '<=', date_to),
                  ('request_change_id.ca_adjustment', '=', True)
                  ]

        att3 = self.env['hr.attendance'].search(domain)
        att3 |= att

        return att3

        # return []

    def get_late_overtime_hour_lines(self, employee_id, date_from, date_to):
        current_start_date = self.date_from
        current_end_date = self.date_to
        overtime = self.env['hr.attendance.overtime'].search([('employee_id', '=', employee_id),
                                                              ('date_approved', '>=', current_start_date),
                                                              ('date_approved', '<=', current_end_date),
                                                              ('state', '=', 'approved'),
                                                              ('start_time', '<=', current_start_date),
                                                              ('end_time', '<=', current_start_date),
                                                              ])
        lockout_period = self.env['ir.config_parameter'].get_param('default.ot.lockout.period', 0)
        late_overtime = self.env['hr.attendance.overtime']
        if self.env['ir.config_parameter'].get_param('default.ot.lockout', False) and lockout_period:
            period_line_ids = self.env['hr.payroll.period_line'].search([], order='start_date')
            for rec in overtime:
                payroll_period = period_line_ids.filtered(lambda l: l.start_date <= rec.date_approved and l.end_date >= rec.date_approved)
                if self.payroll_period_id and payroll_period:
                    time_period = period_line_ids.ids.index(self.payroll_period_id.id) - period_line_ids.ids.index(payroll_period.id)
                    if float(lockout_period) >= time_period:
                        late_overtime |= rec
        attendance_domain = [('employee_id', '=', employee_id),
                             ('overtime_id', 'in', late_overtime.ids)
                             ]
        return self.env['hr.attendance'].search(attendance_domain)

    def get_worked_overtime_hour_lines(self, employee_id, date_from, date_to):
        """Returns overtime."""
        date_f = fields.Datetime.from_string(date_from)
        date_t = fields.Datetime.from_string(date_to)

        start_dt = date_f.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = date_t.replace(hour=7, minute=0, second=59, microsecond=999999) + timedelta(days=1)
        timezone = self.env.user.tz or self._context.get('tz')

        date_from = fields.Datetime.to_string(context_utc(start_dt, timezone))
        date_to = fields.Datetime.to_string(context_utc(end_dt, timezone))

        domain = [('employee_id', '=', employee_id),
                  ('start_time', '>=', date_from),
                  ('end_time', '<=', date_to),
                  ('offset', '=', False),
                  ('state', '=', 'approved')
                  ]

        attendance_overtime = self.env['hr.attendance.overtime'].search(domain)

        domain = [('employee_id', '=', employee_id),
                  ('date_approved', '>=', date_from),
                  ('date_approved', '<=', date_to),
                  ('offset', '=', False),
                  ('state', '=', 'approved'),
                  ('overtime_adjustment', '=', True)
                  ]

        attendance_overtime |= self.env['hr.attendance.overtime'].search(domain)

        attendance_domain = [('employee_id', '=', employee_id),
                             ('overtime_id', 'in', attendance_overtime.ids)
                             ]

        return self.env['hr.attendance'].search(attendance_domain)

    def pull_attendance(self, contract):
        date_from = self.payroll_period_id.start_date
        date_to = self.payroll_period_id.end_date
        worked_hours_ids = self.worked_days_line_ids.browse([])
        #raise ValidationError(_("%s::::::%s:::::" % (self.date_from, self.payroll_period_id.start_date)))
        """Returns attendance record."""
        
        # Searches for leave attendances that are within the payroll period
        leave_attendances = self.env['hr.attendance'].search([
            ('leave_ids.leave_approve_date', '<=', date_to),
            ('leave_ids.leave_approve_date', '>=', date_from),
            ('employee_id', '=', self.employee_id.id), 
        ])

        # Converted leave_attendances to table format
        x = [x for x in leave_attendances]

        # Initializes worked_hours
        worked_hours = self.get_worked_hour_lines(self.employee_id.id, date_from, date_to)
        
        # Subtracts the values of leave_attendances from worked_hours
        late_attendance_list = [item for item in x if item not in worked_hours]

        # Attach the computed values from late_attendance_list to another table inside the payslip (Late Processing)
        self.late_attendances = [(4, i.id, None) for i in late_attendance_list]
        
        # Official Business
        attendances = {
            'name': _("Official Business"),
            'sequence': 1,
            'code': 'OB',
            'number_of_days': 0.0,
            'number_of_hours': 0.0,
            'contract_id': contract.id,
        }

        for record in worked_hours:
            attendances['number_of_days'] += float_round(record.ob_hours / 8.0, precision_digits=2)
            attendances['number_of_hours'] += float_round(record.ob_hours, precision_digits=2)

        worked_hours_ids += worked_hours_ids.new(attendances)

        # Late Official Business
        attendances = {
            'name': _("Late Official Business"),
            'sequence': 1,
            'code': 'LOB',
            'number_of_days': 0.0,
            'number_of_hours': 0.0,
            'contract_id': contract.id,
        }

        for record in late_attendance_list:
            attendances['number_of_days'] += float_round(record.ob_hours / 8.0, precision_digits=2)
            attendances['number_of_hours'] += float_round(record.ob_hours, precision_digits=2)

        worked_hours_ids += worked_hours_ids.new(attendances)
    
        # Leave With Pay
        attendances = {
            'name': _("Leave With Pay"),
            'sequence': 1,
            'code': 'LWP',
            'number_of_days': 0.0,
            'number_of_hours': 0.0,
            'contract_id': contract.id,
        }

        for record in worked_hours:
            attendances['number_of_days'] += float_round(record.leave_hours / 8.0, precision_digits=2)
            attendances['number_of_hours'] += float_round(record.leave_hours , precision_digits=2)

        worked_hours_ids += worked_hours_ids.new(attendances)

        # Late Leave With Pay
        attendances = {
            'name': _("Late Leave With Pay"),
            'sequence': 1,
            'code': 'LateLWP',
            'number_of_days': 0.0,
            'number_of_hours': 0.0,
            'contract_id': contract.id,
        }

        for record in late_attendance_list:
            attendances['number_of_days'] += float_round(record.leave_hours / 8.0, precision_digits=2)
            attendances['number_of_hours'] += float_round(record.leave_hours , precision_digits=2)

        worked_hours_ids += worked_hours_ids.new(attendances)

        # Leave Without Pay
        attendances = {
            'name': _("Leave Without Pay"),
            'sequence': 1,
            'code': 'LWOP',
            'number_of_days': 0.0,
            'number_of_hours': 0.0,
            'contract_id': contract.id,
        }

        for record in worked_hours:
            attendances['number_of_days'] += float_round(record.leave_wop_hours / 8.0, precision_digits=2)
            attendances['number_of_hours'] += float_round(record.leave_wop_hours, precision_digits=2)

        worked_hours_ids += worked_hours_ids.new(attendances)

        # Late Leave Without Pay
        attendances = {
            'name': _("Late Leave Without Pay"),
            'sequence': 1,
            'code': 'LateLWOP',
            'number_of_days': 0.0,
            'number_of_hours': 0.0,
            'contract_id': contract.id,
        }

        for record in late_attendance_list:
            attendances['number_of_days'] += float_round(record.leave_wop_hours / 8.0, precision_digits=2)
            attendances['number_of_hours'] += float_round(record.leave_wop_hours, precision_digits=2)

        worked_hours_ids += worked_hours_ids.new(attendances)

        # Regular Work
        attendances = {
            'name': _("Regular Work"),
            'sequence': 1,
            'code': 'RegWrk',
            'number_of_days': 0.0,
            'number_of_hours': 0.0,
            'contract_id': contract.id,
        }

        for record in worked_hours:
            if record.late_hours > 0.000001:
                late_hours_convert = record.late_hours*60
                domain = [('range1', '<=', late_hours_convert), ('range2', '>=', late_hours_convert)]
                object = self.env['tardiness.table'].search(domain, limit=1)
                equivalent_min = float(object.equivalent_min) / 60

                attendances['number_of_days'] += float_round(1 - (equivalent_min/8), precision_digits=2) 
                attendances['number_of_hours'] += float_round(8 - equivalent_min, precision_digits=2)
            else:
                attendances['number_of_days'] += float_round(record.worked_hours / 8.0, precision_digits=2)
                attendances['number_of_hours'] += float_round(record.worked_hours, precision_digits=2)

        worked_hours_ids += worked_hours_ids.new(attendances)
        
        # Rest Day OT Night Diff
        # rest_day_ot_night_diff = {
        #     'name': _("Rest Day OT Night Diff"),
        #     'sequence': 6,
        #     'code': 'RestDayOTNtdff',
        #     'number_of_days': 0.0,
        #     'number_of_hours': 0.0,
        #     'contract_id': contract.id,
        # }

        # for record in worked_hours:
        #     rest_day_ot_night_diff['number_of_days'] += float_round(record.night_diff_hours / 8.0, precision_digits=2)
        #     rest_day_ot_night_diff['number_of_hours'] += float_round(record.night_diff_hours, precision_digits=2)

        # worked_hours_ids += worked_hours_ids.new(rest_day_ot_night_diff)

        # Special Holiday Night diff
        # special_holiday_night_differential = {
        #     'name': _("Special Holiday Night Differential"),
        #     'sequence': 6,
        #     'code': 'SpNd',
        #     'number_of_days': 0.0,
        #     'number_of_hours': 0.0,
        #     'contract_id': contract.id,
        # }

        # for record in worked_hours:
        #     special_holiday_night_differential['number_of_days'] += float_round(record.night_diff_hours / 8.0, precision_digits=2)
        #     special_holiday_night_differential['number_of_hours'] += float_round(record.night_diff_hours, precision_digits=2)

        # worked_hours_ids += worked_hours_ids.new(special_holiday_night_differential)

        # night diff
        night_differential = {
            'name': _("Night Shift Differential"),
            'sequence': 7,
            'code': 'NightShiftDiff',
            'number_of_days': 0.0,
            'number_of_hours': 0.0,
            'contract_id': contract.id,
        }

        for record in worked_hours:
            night_differential['number_of_days'] += float_round(record.night_diff_hours / 8.0, precision_digits=2)
            night_differential['number_of_hours'] += float_round(record.night_diff_hours, precision_digits=2)

        worked_hours_ids += worked_hours_ids.new(night_differential)

        # Absences
        absences = {
            'name': _("Absences"),
            'sequence': 8,
            'code': 'ABS',
            'number_of_days': 0.0,
            'number_of_hours': 0.0,
            'contract_id': contract.id,
        }

        for record in worked_hours:
            absences['number_of_days'] += float_round(record.absent_hours / 8.0, precision_digits=2)
            absences['number_of_hours'] += float_round(record.absent_hours, precision_digits=2)

        worked_hours_ids += worked_hours_ids.new(absences)

        # undertime
        undertime = {
            'name': _("Undertime"),
            'sequence': 9,
            'code': 'UT',
            'number_of_days': 0.0,
            'number_of_hours': 0.0,
            'contract_id': contract.id,
        }

        for record in worked_hours:
            undertime['number_of_days'] += float_round(record.undertime_hours / 8.0, precision_digits=2)
            undertime['number_of_hours'] += float_round(record.undertime_hours, precision_digits=2)

        worked_hours_ids += worked_hours_ids.new(undertime)

        # LATE
        late = {
            'name': _("Tardiness"),
            'sequence': 10,
            'code': 'TD',
            'number_of_days': 0.0,
            'number_of_hours': 0.0,
            'contract_id': contract.id,
        }
        
        # for retrieval purposes (original code)
        for record in worked_hours:
            late['number_of_days'] += float_round(record.late_hours / 8.0, precision_digits=2)
            late['number_of_hours'] += float_round(record.late_hours, precision_digits=2)

        # New Logic for TD
        #for record in worked_hours:
        #    late_hours_convert = record.late_hours*60
        #    domain = [('range1', '<=', late_hours_convert), ('range2', '>=', late_hours_convert)]
        #    object = self.env['tardiness.table'].search(domain, limit=1)
        #    equivalent_min = float(object.equivalent_min) / 60

         #   late['number_of_days'] += float_round(equivalent_min / 8, precision_digits=2)
          #  late['number_of_hours'] += float_round(equivalent_min, precision_digits=2)
        
        worked_hours_ids += worked_hours_ids.new(late)

        # Rest Day
        rest_day_attendance = {
            'name': _("Rest Day Work"),
            'sequence': 11,
            'code': 'RestDayWrk',
            'number_of_days': 0.0,
            'number_of_hours': 0.0,
            'contract_id': contract.id,
        }

        for record in worked_hours:
            rest_day_attendance['number_of_days'] += float_round(record.rest_day_hours / 8.0, precision_digits=2)
            rest_day_attendance['number_of_hours'] += float_round(record.rest_day_hours, precision_digits=2)

        worked_hours_ids += worked_hours_ids.new(rest_day_attendance)
        # End of rest day

        # Holiday_attendances
        special_holiday_attendances = {
            'name': _("Special Holiday Work"),
            'sequence': 12,
            'code': 'SpeHolWrk',
            'number_of_days': 0.0,
            'number_of_hours': 0.0,
            'contract_id': contract.id,
        }

        for record in worked_hours:
            special_holiday_attendances['number_of_days'] += float_round(record.sp_holiday_hours / 8.0,
                                                                         precision_digits=2)
            special_holiday_attendances['number_of_hours'] += float_round(record.sp_holiday_hours, precision_digits=2)

        worked_hours_ids += worked_hours_ids.new(special_holiday_attendances)

        # holiday_attendances
        regular_holiday_attendances = {
            'name': _("Regular Holiday Work"),
            'sequence': 13,
            'code': 'RegHolWrk',
            'number_of_days': 0.0,
            'number_of_hours': 0.0,
            'contract_id': contract.id,
        }

        for record in worked_hours:
            regular_holiday_attendances['number_of_days'] += float_round(record.reg_holiday_hours / 8.0,
                                                                         precision_digits=2)
            regular_holiday_attendances['number_of_hours'] += float_round(record.reg_holiday_hours, precision_digits=2)

        worked_hours_ids += worked_hours_ids.new(regular_holiday_attendances)

        # Late Overtime
        late_overtime = {
            'name': _("Late Overtime"),
            'sequence': 14,
            'code': 'LOT',
            'number_of_days': 0.0,
            'number_of_hours': 0.0,
            'contract_id': contract.id,
        }

        for record in late_attendance_list:
            late_overtime['number_of_days'] += float_round(record.overtime_hours / 8.0, precision_digits=2)
            late_overtime['number_of_hours'] += float_round(record.overtime_hours, precision_digits=2)

        worked_hours_ids += worked_hours_ids.new(late_overtime)

        # Overtime
        overtime = {
            'name': _("Regular Overtime"),
            'sequence': 14,
            'code': 'RegOT',
            'number_of_days': 0.0,
            'number_of_hours': 0.0,
            'contract_id': contract.id,
        }

        for record in worked_hours:
            overtime['number_of_days'] += float_round(record.overtime_hours / 8.0, precision_digits=2)
            overtime['number_of_hours'] += float_round(record.overtime_hours, precision_digits=2)

        worked_hours_ids += worked_hours_ids.new(overtime)

        # Night differential Overtime
        ndiff_overtime = {
            'name': _("Night Differential"),
            'sequence': 15,
            'code': 'NightDiff',
            'number_of_days': 0.0,
            'number_of_hours': 0.0,
            'contract_id': contract.id,
        }

        for record in worked_hours:
            ndiff_overtime['number_of_days'] += float_round(record.night_diff_ot_hours / 8.0, precision_digits=2)
            ndiff_overtime['number_of_hours'] += float_round(record.night_diff_ot_hours, precision_digits=2)

        worked_hours_ids += worked_hours_ids.new(ndiff_overtime)

        # Rest Day Overtime
        rest_day_overtime = {
            'name': _("Rest Day Overtime"),
            'sequence': 16,
            'code': 'RestOT',
            'number_of_days': 0.0,
            'number_of_hours': 0.0,
            'contract_id': contract.id,
        }

        for record in worked_hours:
            rest_day_overtime['number_of_days'] += float_round(record.rest_day_ot_hours / 8.0, precision_digits=2)
            rest_day_overtime['number_of_hours'] += float_round(record.rest_day_ot_hours, precision_digits=2)

        worked_hours_ids += worked_hours_ids.new(rest_day_overtime)
        # End of rest day overtime

        # Start of Special Holiday Overtime
        special_holiday_overtime = {
            'name': _("Special Holiday Overtime"),
            'sequence': 17,
            'code': 'SpeHolOT',
            'number_of_days': 0.0,
            'number_of_hours': 0.0,
            'contract_id': contract.id,
        }

        for record in worked_hours:
            special_holiday_overtime['number_of_days'] += float_round(record.sp_hday_ot_hours / 8.0, precision_digits=2)
            special_holiday_overtime['number_of_hours'] += float_round(record.sp_hday_ot_hours, precision_digits=2)

        worked_hours_ids += worked_hours_ids.new(special_holiday_overtime)
        # End of Special Holiday Overtime

        # Start of Regular Holiday Overtime
        regular_holiday_overtime = {
            'name': _("Regular Holiday Overtime"),
            'sequence': 18,
            'code': 'RegHolOT',
            'number_of_days': 0.0,
            'number_of_hours': 0.0,
            'contract_id': contract.id,
        }

        for record in worked_hours:
            regular_holiday_overtime['number_of_days'] += float_round(record.reg_hday_ot_hours / 8.0,
                                                                      precision_digits=2)
            regular_holiday_overtime['number_of_hours'] += float_round(record.reg_hday_ot_hours, precision_digits=2)

        worked_hours_ids += worked_hours_ids.new(regular_holiday_overtime)
        self.worked_days_line_ids += worked_hours_ids
        # End of Regular Holiday Overtime

    @api.onchange('employee_id', 'date_from', 'date_to', 'contract_id', 'struct_id', 'payroll_period_id')
    def onchange_employee(self):
        super(HRPayrollAttendance, self).onchange_employee()
        if self.contract_id and self.contract_id.employee_id.id != self.employee_id.id:
            self.worked_days_line_ids = []

            return

        if self.contract_id and self.contract_id.struct_id.id != self.struct_id.id:
            self.worked_days_line_ids = []

            return

        if not self.payroll_period_id:
            return

        # for contract in self.env['hr.contract'].browse(record.contract_id.id):
        # Edited code - start marker
        # self.worked_days_line_ids = []
        # Edited code - end marker
        #hr_attendance_request = self.env['hr.attendance.request'].search([('employee_id','=',self.employee_id.id),('payroll_period_id','=',self.payroll_period_id.id)],limit=1)
        # hr_attendance_request.prepare_payslip()
        self.pull_attendance(self.contract_id)

    @api.multi
    def action_compute_attendance(self):
        """Re-compute work days and inputs, and salary computations."""
        for record in self:
            record.onchange_employee()
            record.onchange_payroll_period()
            record.compute_sheet()

    @api.multi
    def btn_compute_attendance(self):
        self.action_compute_attendance()

    @api.onchange('payroll_period_id')
    def onchange_payroll_period(self):
        if self.payroll_period_id:
            self.date_from = self.payroll_period_id.start_date
            self.date_to = self.payroll_period_id.end_date
            self.date_release = self.payroll_period_id.date_release

    @api.model
    def get_payslip_lines(self, contract_ids, payslip_id):
        def _sum_salary_rule_category(localdict, category, amount):
            if category.parent_id:
                localdict = _sum_salary_rule_category(
                    localdict, category.parent_id, amount)
            if category.code in localdict['categories'].dict:
                amount += localdict['categories'].dict[category.code]
            localdict['categories'].dict[category.code] = amount
            return localdict

        class BrowsableObject(object):
            def __init__(self, employee_id, dict, env):
                self.employee_id = employee_id
                self.dict = dict
                self.env = env

            def __getattr__(self, attr):
                return attr in self.dict and self.dict.__getitem__(attr) or 0.0

        class InputLine(BrowsableObject):
            """a class that will be used into the python code, mainly for usability purposes"""

            def sum(self, code, from_date, to_date=None):
                if to_date is None:
                    to_date = fields.Date.today()
                self.env.cr.execute("""
                    SELECT sum(amount) as sum
                    FROM hr_payslip as hp, hr_payslip_input as pi
                    WHERE hp.employee_id = %s AND hp.state = 'done'
                    AND hp.date_from >= %s AND hp.date_to <= %s AND hp.id = pi.payslip_id AND pi.code = %s""",
                                    (self.employee_id, from_date, to_date, code))
                return self.env.cr.fetchone()[0] or 0.0

        class WorkedDays(BrowsableObject):
            """a class that will be used into the python code, mainly for usability purposes"""

            def _sum(self, code, from_date, to_date=None):
                if to_date is None:
                    to_date = fields.Date.today()
                self.env.cr.execute("""
                    SELECT sum(number_of_days) as number_of_days, sum(number_of_hours) as number_of_hours
                    FROM hr_payslip as hp, hr_payslip_worked_days as pi
                    WHERE hp.employee_id = %s AND hp.state = 'done'
                    AND hp.date_from >= %s AND hp.date_to <= %s AND hp.id = pi.payslip_id AND pi.code = %s""",
                                    (self.employee_id, from_date, to_date, code))
                return self.env.cr.fetchone()

            def sum(self, code, from_date, to_date=None):
                res = self._sum(code, from_date, to_date)
                return res and res[0] or 0.0

            def sum_hours(self, code, from_date, to_date=None):
                res = self._sum(code, from_date, to_date)
                return res and res[1] or 0.0

        class Payslips(BrowsableObject):
            """a class that will be used into the python code, mainly for usability purposes"""

            def sum(self, code, from_date, to_date=None):
                if to_date is None:
                    to_date = fields.Date.today()
                self.env.cr.execute("""SELECT sum(case when hp.credit_note = False then (pl.total) else (-pl.total) end)
                            FROM hr_payslip as hp, hr_payslip_line as pl
                            WHERE hp.employee_id = %s AND hp.state = 'done'
                            AND hp.date_from >= %s AND hp.date_to <= %s AND hp.id = pl.slip_id AND pl.code = %s""",
                                    (self.employee_id, from_date, to_date, code))
                res = self.env.cr.fetchone()
                return res and res[0] or 0.0

        # we keep a dict with the result because a value can be overwritten by
        # another rule with the same code
        result_dict = {}
        rules_dict = {}
        worked_days_dict = {}
        inputs_dict = {}
        blacklist = []
        payslip = self.env['hr.payslip'].browse(payslip_id)
        for worked_days_line in payslip.worked_days_line_ids:
            worked_days_dict[worked_days_line.code] = worked_days_line
        for input_line in payslip.input_line_ids:
            inputs_dict[input_line.code] = input_line

        categories = BrowsableObject(payslip.employee_id.id, {}, self.env)
        inputs = InputLine(payslip.employee_id.id, inputs_dict, self.env)
        worked_days = WorkedDays(
            payslip.employee_id.id, worked_days_dict, self.env)
        payslips = Payslips(payslip.employee_id.id, payslip, self.env)
        rules = BrowsableObject(payslip.employee_id.id, rules_dict, self.env)

        baselocaldict = {'categories': categories, 'rules': rules,
                         'payslip': payslips, 'worked_days': worked_days, 'inputs': inputs,
                         'payslip_rec': payslip}
        # get the ids of the structures on the contracts and their parent id as
        # well
        contracts = self.env['hr.contract'].browse(contract_ids)
        structure_ids = contracts.get_all_structures()
        # get the rules of the structure and thier children
        rule_ids = self.env['hr.payroll.structure'].browse(
            structure_ids).get_all_rules()
        # run the rules by sequence
        sorted_rule_ids = [id for id, sequence in sorted(
            rule_ids, key=lambda x:x[1])]
        sorted_rules = self.env['hr.salary.rule'].browse(sorted_rule_ids)

        for contract in contracts:
            employee = contract.employee_id
            localdict = dict(baselocaldict, employee=employee,
                             contract=contract)
            for rule in sorted_rules:
                key = rule.code + '-' + str(contract.id)
                localdict['salary_rule'] = rule
                localdict['result'] = None
                localdict['result_qty'] = 1.0
                localdict['result_rate'] = 100
                # check if the rule can be applied
                if rule.satisfy_condition(localdict) and rule.id not in blacklist:
                    # compute the amount of the rule
                    amount, qty, rate = rule.compute_rule(localdict)
                    # check if there is already a rule computed with that code
                    previous_amount = rule.code in localdict and localdict[rule.code] or 0.0
                    # set/overwrite the amount computed for this rule in the
                    # localdict
                    tot_rule = amount * qty * rate / 100.0
                    localdict[rule.code] = tot_rule
                    rules_dict[rule.code] = rule
                    # sum the amount for its salary category
                    localdict = _sum_salary_rule_category(
                        localdict, rule.category_id, tot_rule - previous_amount)
                    # create/overwrite the rule in the temporary results
                    result_dict[key] = {
                        'salary_rule_id': rule.id,
                        'contract_id': contract.id,
                        'name': rule.name,
                        'code': rule.code,
                        'category_id': rule.category_id.id,
                        'sequence': rule.sequence,
                        'appears_on_payslip': rule.appears_on_payslip,
                        'condition_select': rule.condition_select,
                        'condition_python': rule.condition_python,
                        'condition_range': rule.condition_range,
                        'condition_range_min': rule.condition_range_min,
                        'condition_range_max': rule.condition_range_max,
                        'amount_select': rule.amount_select,
                        'amount_fix': rule.amount_fix,
                        'amount_python_compute': rule.amount_python_compute,
                        'amount_percentage': rule.amount_percentage,
                        'amount_percentage_base': rule.amount_percentage_base,
                        'register_id': rule.register_id.id,
                        'amount': amount,
                        'employee_id': contract.employee_id.id,
                        'quantity': qty,
                        'rate': rate,
                    }
                else:
                    # blacklist this rule and its children
                    blacklist += [id for id,
                                  seq in rule._recursive_search_of_rules()]

        return [value for code, value in result_dict.items()]

    @api.multi
    def compute_sheet(self):
        res = super(HRPayrollAttendance, self).compute_sheet()
        enable_netcap = self.env['ir.config_parameter'].get_param('net.cap.enable', False)

        if enable_netcap:
            self.compute_netcap_limit()

        return res

    def is_salary_prorated(self, payslip):
        if payslip.contract_id:

            domain = [
                ('contract_id', '=', payslip.contract_id.id),
                ('date_start', '<=', payslip.date_to),
                ('date_end', '>=', payslip.date_from)
            ]

            salary_move = self.env['hr.salary.move'].search(domain)
            domain = [
                ('contract_id', '=', payslip.contract_id.id),
                ('date_start', '<=', payslip.date_to),
                ('date_end', '=', False)
            ]

            salary_move |= self.env['hr.salary.move'].search(domain)
            prorate_cutoff = []
            for record in salary_move.sorted(key=lambda r: r.date_start, reverse=True):

                if payslip.date_from <= record.date_start <= payslip.date_to:
                    date_start = record.date_start
                else:
                    date_start = payslip.date_from

                if payslip.date_from <= record.date_end <= payslip.date_to:
                    date_end = record.date_end

                else:
                    date_end = payslip.date_to

                prorate_cutoff.append((date_start, date_end, record.amount))

            return prorate_cutoff

    @api.multi
    def compute_sheet1(self):
        for payslip in self:
            number = payslip.number or self.env['ir.sequence'].next_by_code('salary.slip')
            # delete old payslip lines
            payslip.line_ids.unlink()
            # set the list of contract for which the rules have to be applied
            # if we don't give the contract, then the rules to apply should be for all current contracts of the employee
            contract_ids = payslip.contract_id.ids or \
                self.get_contract(payslip.employee_id, payslip.date_from, payslip.date_to)

            # check if salary falls on proration
            res = self.is_salary_prorated(payslip)
            # we validate if there is two or more salary movement between cutoff
            if len(res) > 0:

                class ProrateSalary(object):

                    def __init__(self, payslip, cutoff):
                        self.payslip = payslip
                        self.cutoff = cutoff

                    def prorate(self):
                        old_date_from, old_date_to, wage = payslip.date_from, payslip.date_to, payslip.contract_id.wage

                        salary_rates = payslip.get_prorated_computation()
                        emp = payslip.env['hr.employee'].search([('id', '=', payslip.employee_id.id)])
                        contract = payslip.env['hr.contract'].search([('id', '=', emp.contract_id.id)])
                        new_payslip_list = []

                        for value in self.cutoff:
                            date_from, date_to, amount = value
                            setattr(self.payslip, 'date_from', date_from)
                            setattr(self.payslip, 'date_to', date_to)
                            setattr(self.payslip.contract_id, 'wage', amount)

                            payslip.onchange_employee()
                            new_payslip_list += payslip.get_payslip_lines(contract_ids, payslip.id)

                        ontxinc_ids = payslip.get_categ_ids(1)  # ONTXINC
                        othertaxinc_ids = payslip.get_categ_ids(2)  # OtherTaxInc
                        wtx_ids = payslip.get_categ_ids(3)  # WTX
                        ntp_ids = payslip.get_categ_ids(4)  # DED,EMRCTRB,GTP,ONTXDED
                        totear_ids = payslip.get_categ_ids(5)  # ALW,MTH13,BON,ONTXINC,OtherTAXINC
                        fnp_ids = payslip.get_categ_ids(6)  # LON,OtherTaxDed

                        # code name
                        basicsm_name = payslip.get_code_name(7)
                        gross_name = payslip.get_code_name(9)
                        netpay_name = payslip.get_code_name(4)
                        total_earnings_name = payslip.get_code_name(5)
                        npat_name = payslip.get_code_name(10)
                        finalnetpay_name = payslip.get_code_name(6)

                        blacklist = ['HDMF-M', 'SSS-M', 'PHIC-M']
                        new_payslip = {}
                        basic = []
                        other_non_taxable = 0.0
                        other_taxable = 0.0  # this will capture gross,total basic
                        ntp_deductions = 0.0
                        other_deductions = 0.0
                        wtx = 0.0
                        earnings = 0.0
                        fnp_deductions = 0.0
                        for record in new_payslip_list:

                            if record['code'] == basicsm_name:
                                basic.append(record['amount'])
                            if record['code'] in blacklist:
                                other_deductions += record['amount']
                            if record['category_id'] in othertaxinc_ids:
                                other_taxable += record['amount']
                            if record['category_id'] in wtx_ids:
                                wtx += record['amount']
                            if record['category_id'] in ntp_ids:
                                amount = record['amount']
                                ntp_deductions += abs(amount)
                            if record['category_id'] in totear_ids:
                                amount = record['amount']
                                earnings += abs(amount)
                            if record['category_id'] in ontxinc_ids:
                                other_non_taxable += record['amount']
                            if record['category_id'] in fnp_ids:
                                amount = record['amount']
                                fnp_deductions += amount

                            if record['code'] in new_payslip:
                                new_payslip[record['code']]['amount'] = new_payslip[record['code']].get('amount', 0) + record['amount']
                            else:
                                new_payslip[record['code']] = record
                        # check contract type and return value of total deduction and other income
                        deductions = payslip.check_contract_type(contract, other_deductions)
                        othernontaxable = payslip.check_contract_type(contract, other_non_taxable)
                        fnp_ded = payslip.check_contract_type(contract, fnp_deductions)
                        ntp_ded = ntp_deductions - abs(deductions)
                        # print 'total deductions',ntp_ded,'other ded',other_deductions
                        # print 'total earnings',earnings
                        """this will get prorated basic in method get_employee_contract_details"""
                        prorated_basic = payslip.get_prorated_computation()
                        final_basic_rate = prorated_basic.get('salary', None)  # prorated salary
                        gross = final_basic_rate + other_taxable  # gross
                        final_ntp = gross - abs(ntp_ded)  # net taxable pay
                        ntp_after_tax = final_ntp - (wtx)
                        total_earnings = final_basic_rate + earnings - abs(othernontaxable)
                        final_net_pay = final_ntp + othernontaxable - abs(fnp_ded)
                        payslip_list = []

                        for k, v in new_payslip.items():

                            if k == basicsm_name:
                                v['amount'] = final_basic_rate
                            if k == gross_name:
                                v['amount'] = gross
                            if k == netpay_name:
                                v['amount'] = abs(final_ntp)
                            if k == npat_name:
                                v['amount'] = ntp_after_tax
                            if k == total_earnings_name:
                                v['amount'] = total_earnings
                            if k == 'TotDed':
                                v['amount'] = ntp_ded
                            if k == finalnetpay_name:
                                v['amount'] = final_net_pay

                            payslip_list.append(v)

                        lines = [(0, 0, line) for line in payslip_list]
                        payslip.write({'line_ids': lines, 'number': number})
                        # SET BACK TO OLD CONFIGURATION
                        setattr(self.payslip, 'date_from', old_date_from)
                        setattr(self.payslip, 'date_to', old_date_to)
                        setattr(self.payslip.contract_id, 'wage', wage)

                ProrateSalary(payslip, res).prorate()

            else:
                lines = [(0, 0, line) for line in self.get_payslip_lines(contract_ids, payslip.id)]
                payslip.write({'line_ids': lines, 'number': number})

        return True

    def compute_salary_prorate(self, basic_amounts):
        """Compute Basic rates"""
        if len(basic_amounts) == 2:
            before_amount = basic_amounts[0]
            after_amount = basic_amounts[-1]
            diff = before_amount - after_amount
            basic = after_amount + abs(diff)

            return basic

        else:

            return basic_amounts[0]

    def get_categ_id(self, contract, category_code):
        for rec in contract:
            for val in rec.struct_id.rule_ids:
                if val.category_id.code == category_code:

                    return val.category_id.id
                    break

    def get_categ_ids(self, num):
        prorate_structure = self.env['hr.prorate_config'].search([('des_name', '=', num)])
        category_ids = []
        for cat in prorate_structure.categ_line_ids:
            if cat:
                category_ids.append(cat.id)
        return category_ids

    def get_code_name(self, num):
        prorate_structure = self.env['hr.prorate_config'].search([('des_name', '=', num)])
        if prorate_structure:
            return prorate_structure.code

    def check_contract_type(self, contract, value):
        for val in contract:
            if val.schedule_pay == 'bi-monthly':
                return value / 2
            elif val.schedule_pay == 'monthly':
                return value
            else:
                return value

    """start pro rate override"""

    def get_prorated_computation(self):
        for rec in self:
            contract_details = rec.get_employee_contract_details(rec.employee_id.id)
            return contract_details

#     def get_basic_adjustment_deduction(self,emp_id):
#         payroll_start_date = self.payroll_period_id.start_date
#         payroll_end_date = self.payroll_period_id.end_date
#         emp = self.env['hr.employee'].search([('id','=',emp_id)])
#         contract = self.env['hr.contract'].search([('id','=',emp.contract_id.id)])
#         salary = contract.salary_move.filtered(lambda l: (not l.date_end and l.date_start and l.date_start <= payroll_end_date) or
#                                                (l.date_start and l.date_end and l.date_start >= payroll_start_date and l.date_end <= payroll_end_date))
#         return {'deduction': 2000}

    def get_employee_contract_details(self, emp_id):
        emp = self.env['hr.employee'].search([('id', '=', emp_id)])
        contract = self.env['hr.contract'].search([('id', '=', emp.contract_id.id)])
        d = []
        con_det = []
        for details in contract:
            for records in details.salary_move:
                rec = {'AMOUNT': records.amount, 'DATE': records.date_start}
                d.append(rec)
            contract_details = {'AVG_WORKING_DAYS': details.average_working_days}
            con_det.append(contract_details)
        rec_len = len(d)
        if rec_len >= 2:
            if d:
                get_con_det = con_det[0]
                get_current_salary = d[0]
                get_last_salary = d[-1]
                avg_working_days = get_con_det.get('AVG_WORKING_DAYS', None)
                payroll_start_date = self.payroll_period_id.start_date
                payroll_end_date = self.payroll_period_id.end_date
                get_cut_off_date = get_current_salary.get('DATE', None)
                current_salary = get_current_salary.get('AMOUNT', None)
                last_salary = get_last_salary.get('AMOUNT', None)

                # this will determine date if prorate
                if payroll_start_date < get_cut_off_date <= payroll_end_date:

                    start_date = datetime.strptime(payroll_start_date, "%Y-%m-%d")
                    cut_off_date = datetime.strptime(get_cut_off_date, "%Y-%m-%d")
                    end_date = datetime.strptime(payroll_end_date, "%Y-%m-%d")
                    # set end dates
                    cut_off_new_date = self.set_date(cut_off_date)
                    end_date_new_date = self.set_date(end_date)
                    # computed days from cut off
                    days_before = abs((start_date - cut_off_date)).days
                    days_after = abs((cut_off_date - end_date)).days
                    # total records from attendance from cut off date
                    get_attendance_records_before_cut_off = self.get_attendace_records_before(emp_id, payroll_start_date, str(cut_off_date))
                    get_attendance_records_after_cut_off = self.get_attendace_records_after(emp_id, get_cut_off_date, str(end_date_new_date))
                    # print get_attendance_records_after_cut_off,get_attendance_records_before_cut_off
                    # computed rate from cut off
                    rate_before = self.compute_prorate_before(get_attendance_records_before_cut_off, avg_working_days,
                                                              days_before, last_salary)
                    rate_after = self.compute_prorate_after(get_attendance_records_after_cut_off, avg_working_days,
                                                            days_after, current_salary)
                    salary_rate_before = rate_before.get('rate_before_cutoff', None)
                    salary_rate_after = rate_after.get('rate_after_cutoff', None)
                    # print 'salary after and before',salary_rate_after ,salary_rate_before
                    computed_salary = salary_rate_before + salary_rate_after
                    return {'salary': computed_salary}
                else:
                    return {'salary': 0.0}
            else:
                return {'salary': 0.0}

    def compute_prorate_before(self, vals_before, a_w_d, computed_days_before_cutoff, last_salary):
        '''this will compute daily workdays * daily rate'''
        records = vals_before
        num_days = computed_days_before_cutoff

        salary = last_salary
        avg_working_days = a_w_d
        counted_working_days = records.get('weekdays', None)

        daily_rate = self.compute_daily_rate(salary, avg_working_days)
        rate_before_cutoff = counted_working_days * daily_rate
        #overtime_rate = over_time*((salary/avg_working_days)/8.0)*1.25
        return {'rate_before_cutoff': rate_before_cutoff}

    def compute_prorate_after(self, vals_after, a_w_d, computed_days_after_cutoff, current_salary):
        records = vals_after

        counted_working_days = records.get('weekdays', None)
        salary = current_salary
        avg_working_days = a_w_d

        daily_rate = self.compute_daily_rate(salary, avg_working_days)
        rate_after_cutoff = counted_working_days * daily_rate
        #overtime_rate = over_time*((salary/avg_working_days)/8.0)*1.25
        return {'rate_after_cutoff': rate_after_cutoff}

    def get_attendace_records_before(self, employee_id, start_date, end_date):
        """this will return the working days in the specified date"""
        attendances = self.env['hr.attendance'].search([('employee_id', '=', employee_id), ('check_in', '>=', start_date),
                                                       ('check_in', '<', end_date)])

        s_year = datetime.strptime(start_date, '%Y-%m-%d').strftime('%Y')
        s_mnth = datetime.strptime(start_date, '%Y-%m-%d').strftime('%m')
        s_day = datetime.strptime(start_date, '%Y-%m-%d').strftime('%d')

        year = datetime.strptime(end_date, '%Y-%m-%d %H:%M:%S').strftime('%Y')
        mnth = datetime.strptime(end_date, '%Y-%m-%d %H:%M:%S').strftime('%m')
        day = datetime.strptime(end_date, '%Y-%m-%d %H:%M:%S').strftime('%d')

        start = date(int(s_year), int(s_mnth), int(s_day))
        end = date(int(year), int(mnth), int(day))

        weekdays = rrule.rrule(rrule.DAILY, byweekday=range(0, 5), dtstart=start, until=end)
        weekdays = len(list(weekdays))
        if int(time.strftime('%H')) >= 18:
            weekdays -= 1
        # print 'weekdays before',weekdays
        return {'weekdays': weekdays - 1}

    def get_attendace_records_after(self, employee_id, start_date, end_date):
        """this will return the working days in the specified date"""
        attendaces = self.env['hr.attendance'].search([('employee_id', '=', employee_id), ('check_in', '>=', start_date),
                                                       ('check_in', '<=', end_date)])
        s_year = datetime.strptime(start_date, '%Y-%m-%d').strftime('%Y')
        s_mnth = datetime.strptime(start_date, '%Y-%m-%d').strftime('%m')
        s_day = datetime.strptime(start_date, '%Y-%m-%d').strftime('%d')

        year = datetime.strptime(end_date, '%Y-%m-%d %H:%M:%S').strftime('%Y')
        mnth = datetime.strptime(end_date, '%Y-%m-%d %H:%M:%S').strftime('%m')
        day = datetime.strptime(end_date, '%Y-%m-%d %H:%M:%S').strftime('%d')

        start = date(int(s_year), int(s_mnth), int(s_day))
        end = date(int(year), int(mnth), int(day))

        weekdays = rrule.rrule(rrule.DAILY, byweekday=range(0, 5), dtstart=start, until=end)
        weekdays = len(list(weekdays))
        if int(time.strftime('%H')) >= 18:
            weekdays -= 1
        # print 'weekdays after',weekdays
        return {'weekdays': weekdays}

    def get_contract_other_income(self, contract):
        total_other_income = 0.0
        for val in contract.other_income_line:
            if val:
                if contract.schedule_pay == 'bi-monthly':
                    other_income = contract.get_other_income(val.code, self, self.employee_id, contract) / 2
                    total_other_income += other_income
                elif contract.schedule_pay == 'monthly':
                    other_income = contract.get_other_income(val.code, self, self.employee_id, contract) / 2
                    total_other_income += other_income
            else:
                return 0.0
        return total_other_income

    def set_date(self, date):
        new_date = date.replace(hour=23, minute=59, second=59)
        return new_date

    def compute_daily_rate(self, wage, avg_working_days):
        daily_rate = wage / avg_working_days
        return daily_rate

    def compute_hourly_rate(self, daily_rate):
        hourly_rate = daily_rate / 8.0
        return hourly_rate

    def set_number_of_days_before_cutoff(self, num_days_after_cut_off, contract):
        for rec in contract:
            if rec.schedule_pay == 'bi-monthly':
                diff_days = 15 - abs(num_days_after_cut_off)
                return abs(diff_days)
            elif rec.schedule_pay == 'monthly':
                diff_days = 30 - abs(num_days_after_cut_off)
                return abs(diff_days)
    """end """

    def compute_netcap_limit(self):
        """Computes percentage of basic pay then compares to take home pay."""
        percentage = float(self.env['ir.config_parameter'].get_param('net.cap.percentage', '0'))

        basic = sum(self.line_ids.filtered(lambda r: r.salary_rule_id.net_cap_basic).mapped('total'))
        take_home_pay = sum(self.line_ids.filtered(lambda r: r.salary_rule_id.net_cap_total).mapped('total'))

        minimum = basic * (percentage / 100.0)

        if take_home_pay < minimum:
            self.write({'net_cap': True})
        else:
            self.write({'net_cap': False})

    payroll_period_id = fields.Many2one('hr.payroll.period_line', 'Payroll Period', required=True)
    date_release = fields.Date('Date Release', required=True)


class HRPayslipInput(models.Model):
    _inherit = 'hr.payslip.input'

    adjustment_type = fields.Selection([('EarTax', 'Taxable Earnings'),
                                        ('EarNonTax', 'Non Taxable Earnings'),
                                        ('DedTax', 'Taxable Deductions'),
                                        ('DedNonTax', 'Non Taxable Deductions')
                                        ], 'Salary Adjustment Type', require=True)

    name = fields.Char(string='Name', required=True)
    sequence = fields.Integer(required=False, index=True, default=10)

    @api.onchange('adjustment_type')
    def onchange_code(self):
        self.code = self.adjustment_type


class HRPayslipRun(models.Model):
    _inherit = 'hr.payslip.run'

    @api.onchange('payroll_period_id')
    def onchange_payroll_period(self):
        if self.payroll_period_id:
            self.date_start = self.payroll_period_id.start_date
            self.date_end = self.payroll_period_id.end_date
            self.date_release = self.payroll_period_id.date_release

    @api.multi
    def close_payslip_run(self):
        payslips = self.env['hr.payslip']
        for record in self.slip_ids.filtered(lambda r: r.state == 'draft'):
            payslips += record
        payslips.action_payslip_done()
        return self.write({'state': 'close'})

    date_release = fields.Date('Date Release', required=True)
    payroll_period_id = fields.Many2one('hr.payroll.period_line', 'Payroll Period', required=True)


class PayrollPeriod(models.Model):
    _name = 'hr.payroll.period'
    _description = 'Payroll Period'

    @api.multi
    def name_get(self):
        res = []
        for record in self:
            if record.start_date and record.end_date:
                date_start = fields.Date.from_string(record.start_date).strftime('%B %d, %Y')
                date_end = fields.Date.from_string(record.end_date).strftime('%B %d, %Y')
                name = "{} - {}".format(date_start, date_end)
                res.append((record.id, name))

        return res

    @api.constrains('start_date', 'end_date')
    def _check_validity_start_end_date(self):
        """ verifies if start date is earlier than end date period. """
        for period in self:
            if period.start_date and period.end_date:
                if period.end_date < period.start_date:
                    raise ValidationError(_('"End date" date cannot be earlier than "Start date" date.'))

    @api.multi
    def btn_generate_period(self):
        pass

    start_date = fields.Date('Start Date', required=True)
    end_date = fields.Date('End Date', required=True)
    active = fields.Boolean('Active', default=True)
    period_line = fields.One2many('hr.payroll.period_line', 'payroll_period_id', 'Period Line')
    type_hire = fields.Selection([("Direct", "Direct"), ("Agency", "Agency")], string="Type Hire", required=True)


class PayrollPeriodLine(models.Model):
    _name = 'hr.payroll.period_line'
    _order = 'start_date DESC'
    _description = 'Payroll Period Line'

    @api.multi
    def name_get(self):
        res = []
        for record in self:
            if record.start_date and record.end_date:
                date_start = fields.Date.from_string(record.start_date).strftime('%B %d, %Y')
                date_end = fields.Date.from_string(record.end_date).strftime('%B %d, %Y')
                name = "{} - {}".format(date_start, date_end)
                res.append((record.id, name))

        return res

    @api.constrains('start_date', 'end_date')
    def _check_period(self):
        for record in self:
            if record.end_date < record.start_date:
                raise ValidationError(_('"End date" date cannot be earlier than "Start date" date.'))

    @api.constrains('start_date', 'end_date')
    def check_dulplicate_payroll_dates(self):
        for rec in self:
            dates = rec.payroll_period_id.period_line.filtered(lambda l: (rec.id != l.id) and (rec.start_date >= l.start_date and rec.start_date <= l.end_date) or
                                                               (rec.end_date >= l.start_date and rec.end_date <= l.end_date)
                                                               or (rec.start_date <= l.start_date and (rec.end_date <= l.end_date or rec.end_date >= l.end_date)))
            if len(dates) > 1:
                raise ValidationError("These payroll period already exists!!")

    @api.constrains('end_date', 'date_release')
    def _check_release_date(self):
        for record in self:
            if record.date_release < record.end_date:
                raise ValidationError(_('"Release date" date cannot be earlier than "End date" date.'))

    @api.multi
    def action_set_period(self):
        active_id = self._context.get('active_id')
        active_model = self._context.get('active_model')

        for record in self:
            if active_id and active_model == 'hr.payslip':
                payroll = self.env[active_model].browse(active_id)
                payroll.write({'date_from': record.start_date,
                               'date_to': record.end_date,
                               'date_release': record.date_release})
                # recompute attendance
                payroll.action_compute_attendance()

            if active_id and active_model == 'hr.payslip.run':
                payroll_run = self.env[active_model].browse(active_id)
                payroll_run.write({'date_start': record.start_date,
                                   'date_end': record.end_date,
                                   'date_release': record.date_release})

    @api.multi
    def action_set_generate_attendances(self):
        for record in self:
            self.env['hr.attendance'].make_absent(record.start_date, record.end_date)
        return True

    def btn_set_period(self):
        self.action_set_period()

    def btn_generate_attendances(self):
        self.action_set_generate_attendances()
        return True

    @api.constrains('start_date', 'end_date')
    def _check_validity_start_end_date(self):
        """ verifies if start date is earlier than end date period. """
        for period in self:
            if period.start_date and period.end_date:
                if period.end_date < period.start_date:
                    raise ValidationError(_('"End date" time cannot be earlier than "Start date" date.'))

    cut_off = fields.Selection([(1, 1), (2, 2),(3,3),(4,4)], string="Cut Off", required=True)
    start_date = fields.Date('Start Date', required=True)
    end_date = fields.Date('End Date', required=True)
    date_release = fields.Date('Release Date', required=True)
    payroll_period_id = fields.Many2one('hr.payroll.period', 'Period', ondelete='cascade')


class HRYearToDate(models.Model):
    _name = 'hr.year_to_date'
    _description = 'Year to Date'
    _rec_name = 'employee_id'

    @api.model
    def create(self, vals):
        create_employee = vals.get('employee_id')
        create_ytd = vals.get('ytd_date')

        if create_employee and create_ytd:
            exist_record = self.search([
                ('employee_id', '=', create_employee),
                ('ytd_date', '=', create_ytd)
            ], limit = 1)
            if exist_record:
                employee_name = self.env['hr.employee'].browse(create_employee).name
                raise ValidationError(_("This employee '%s' record is already exist." %(employee_name)))
            
        return super(HRYearToDate, self).create(vals)

    def write(self, vals):
        res = super(HRYearToDate, self).write(vals)

        # Check if 'old_ytd_amount' is in vals and if 'employee_id' is present in the current record
        if 'old_ytd_amount' in vals and 'employee_id' in vals:
            write_employee = vals['employee_id']
            write_ytd = vals['ytd_date']

            if 'employee_id' in vals and 'ytd_date' in vals:
                exist_record = self.search([
                    ('employee_id', '=', write_employee),
                    ('ytd_date', '=', write_ytd),
                    ('id', '!=', self.id)
                ], limit=1)
                if exist_record:
                    employee_name = self.env['hr.employee'].browse(write_employee).name
                    raise ValidationError(_("This employee '%s' record is already exist." %(employee_name)))
                
            employee_id = vals['employee_id']
            old_ytd_amount = vals['old_ytd_amount']

            # Update 'old_ytd_amount' for related hr.year_to_date records
            ytd_records = self.env['hr.year_to_date.line'].search([('ytd_to_date_id.employee_id', '=', employee_id)], limit=1)
            ytd_records.write({'old_ytd_amount': old_ytd_amount})

        return res

    @api.model
    def default_get(self, fields_list):
        res = super(HRYearToDate, self).default_get(fields_list)
        config_lines = []

        for record in self.env['hr.year_to_date.config'].search([]):
            config_lines.append((0, 0, {'ytd_config_id': record.id}))

        res['year_to_date_line'] = config_lines
        res = self._convert_to_write(res)
        return res

    employee_id = fields.Many2one('hr.employee', 'Employee', required=True)
    ytd_date = fields.Date('Date', required=True)
    year_to_date_line = fields.One2many('hr.year_to_date.line', 'ytd_to_date_id', 'Year to Date Line')
    previous_employer = fields.Char('Previous Employer Name')
    previous_employer_tin = fields.Char('Previous Employer TIN')
    previous_employer_address = fields.Char('Previous Employer Address')
    zip_code = fields.Char('ZIP Code')


class HRYearToDateLine(models.Model):
    _name = 'hr.year_to_date.line'
    _description = 'Year to Date Lines'

    def get_previous_ytd(self, employee_id, ytd_config_ids, date_from, date_to):
        """Returns dictionary of ytd configuration id."""
        domain = [
            ('ytd_to_date_id.employee_id', '=', employee_id),
            ('ytd_to_date_id.ytd_date', '>=', date_from),
            ('ytd_to_date_id.ytd_date', '<=', date_to),
            ('ytd_config_id', 'in', ytd_config_ids)
        ]

        res = self.read_group(domain, ['ytd_config_id', 'old_ytd_amount'], ['ytd_config_id'])
        data = dict((r['ytd_config_id'][0], r['old_ytd_amount']) for r in res)

        return data

    @api.depends('ytd_config_id', 'ytd_config_id.salary_rule_ids', 'old_ytd_amount')
    def _compute_amount_total(self):
        for record in self:
            if record.ytd_to_date_id and record.ytd_to_date_id.employee_id:
                date_from = from_string(record.ytd_to_date_id.ytd_date)
                date_to = to_string(date_from.replace(month=12, day=31, year=int(date_from.strftime('%Y'))))
                current_ytd_amount = sum([self.env['hr.payslip'].
                                          get_year_to_date(record.ytd_to_date_id.employee_id.id, record.ytd_to_date_id.ytd_date, date_to).
                                          get(r, 0) for r in record.ytd_config_id.salary_rule_ids.ids])
                if self.env.ref('hris.hr_rule_TaxDue') in record.ytd_config_id.salary_rule_ids:
                    ntp_line = record.ytd_to_date_id.year_to_date_line.filtered(lambda l: self.env.ref('hris.hr_rule_NTP').id in l.ytd_config_id.salary_rule_ids.ids)[0]
                    amount = ntp_line.current_ytd_amount + ntp_line.old_ytd_amount
                    range = self.env['annual.income.tax'].search([('min_range', '<=', amount),
                                                                  ('max_range', '>=', amount)], limit=1)
                    current_ytd_amount = ((amount - range.min_range) * (range.percentage / 100)) + range.prescribed_tax
                record.current_ytd_amount = current_ytd_amount
                record.amount_total = record.current_ytd_amount + record.old_ytd_amount
            else:
                record.current_ytd_amount = 0
                record.amount_total = 0

    ytd_to_date_id = fields.Many2one('hr.year_to_date', 'Year to Date')
    ytd_config_id = fields.Many2one('hr.year_to_date.config', 'Year to Date', required=True)
    current_ytd_amount = fields.Float('Current Year to Date Amount', compute='_compute_amount_total')
    old_ytd_amount = fields.Float('Old Year to Date Amount')
    amount_total = fields.Float('Total', compute='_compute_amount_total')