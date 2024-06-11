from odoo import models, fields, api
from odoo.tools import float_compare
from odoo.exceptions import ValidationError, UserError
from odoo.tools.translate import _
from datetime import datetime, timedelta, date
from dateutil.relativedelta import relativedelta
from dateutil.rrule import rrule, DAILY
import math


def convert_time_to_float(time):
    hours = time.seconds // 3600
    minutes = (time.seconds // 60) % 60
    total_hrs = float(minutes) / 60 + hours
    return total_hrs


def float_time_convert(float_value):
    """Splits float value into hour and minute."""
    minute, hour = math.modf(float_value)
    minute = minute * 60
    hour = int(round(hour, 2))
    minute = int(round(minute, 2))
    return hour, minute


def checking_holiday_setting(holiday_date_data):
    holiday_type = []
    for holiday_type_setting in holiday_date_data:
        if holiday_type_setting.holiday_type == 'regular' and holiday_type_setting.before == True:
            holiday_type.append(1)
        if holiday_type_setting.holiday_type == 'regular' and holiday_type_setting.before == False:
            holiday_type.append(2)
        if holiday_type_setting.holiday_type == 'special' and holiday_type_setting.before == True:
            holiday_type.append(3)
        if holiday_type_setting.holiday_type == 'special' and holiday_type_setting.before == False:
            holiday_type.append(4)

    return holiday_type


def intersection_list(list1, list2):
    return list(set(list1) & set(list2))


class LeavesAutomate(models.Model):
    _inherit = 'hr.holidays'




class AdditionalTables(models.Model):
    _inherit = 'payroll.sss.contribution'

    wsip_er = fields.Float(string="WISP (ER)")
    wsip_ee = fields.Float(string="WISP (EE)")
    
    total_er = fields.Float(string="TOTAL (ER)", 
                            default=0, 
                            compute="_compute_total_amount", 
                            required=True,
                            store=True)
    
    total_ee = fields.Float(string="TOTAL (EE)", 
                            default=0, 
                            compute="_compute_total_amount", 
                            required=True,
                            store=True)
    
    total_amount = fields.Float(string="Total Contribution", 
                            default=0, 
                            compute="_compute_total_amount", 
                            required=True,
                            store=True)
    
    @api.depends('wsip_er', 'wsip_ee', 'total_er', 'total_ee', 'contrib_ee', 'contrib_er')
    def _compute_total_amount(self):
        for record in self:
            record.total_er = record.contrib_er + record.wsip_er
            record.total_ee = record.contrib_ee + record.wsip_ee 
            record.total_amount = record.total_er + record.total_ee
    

class SalaryRulesAdditional(models.Model):
    _inherit = 'hr.contract'
   
    def get_prev_phic(self,contract,payslip):
        domain = [('date_release', '<', payslip.date_release), ('contract_id', '=', contract.id), ('contract_id.employee_id', '=', contract.employee_id.id)]
        prev_payslip = self.env['hr.payslip'].search(domain, limit=1, order="date_release DESC")

        total = 0
        for pays in prev_payslip:
            total += pays.line_ids.filtered(lambda x: x.code == 'PHIC-FC').amount

        return total
    def sss_logic(self, contract, payslip):
        domain = [('date_release', '<', payslip.date_release), ('contract_id', '=', contract.id), ('contract_id.employee_id', '=', contract.employee_id.id)]
        prev_payslip = self.env['hr.payslip'].search(domain, limit=3, order="date_release DESC")

        total = 0
        for pays in prev_payslip:
            total += pays.line_ids.filtered(lambda x: x.code == 'SSS-FCO').amount
        
        return total
    def get_prev_regwrk(self, contract, payslip):
        domain = [('date_release', '<', payslip.date_release), ('contract_id', '=', contract.id), ('contract_id.employee_id', '=', contract.employee_id.id)]
        prev_payslip = self.env['hr.payslip'].search(domain, limit=1, order="date_release DESC")
        if prev_payslip:
            datas = {'RegWrk': 0.0}
            for line in prev_payslip.line_ids:
                if line.code in datas:
                    datas[line.code] += line.total
                else:
                    continue
            total_prev_regwrk = datas['RegWrk']
            if total_prev_regwrk:
                return total_prev_regwrk
        return 0.0

    def get_prev_gross(self, contract, payslip):
        domain = [('date_release', '<', payslip.date_release), ('contract_id', '=', contract.id), ('contract_id.employee_id', '=', contract.employee_id.id)]
        prev_payslip = self.env['hr.payslip'].search(domain, limit=1, order="date_release DESC")
        if prev_payslip:
            datas = {'GTP': 0.0}
            for line in prev_payslip.line_ids:
                if line.category_id.code in datas:
                    datas[line.category_id.code] += line.total
                else:
                    continue
            total_prev_regwrk = datas['GTP']
            if total_prev_regwrk:
                return total_prev_regwrk
        return 0.0

    def meal_allowance_revised(self, contract, date_from, date_to):
        date_from_converted = datetime.strptime(date_from, '%Y-%m-%d').date()
        date_to_converted = datetime.strptime(date_to, '%Y-%m-%d').date()
        # get_attendance = self.env['hr.attendance'].search([('employee_id', '=', contract.employee_id['id']), ('check_in', '>=', str(date_from_converted)),
        #                                                    ('check_in', '<=', str(date_to_converted))])
        # worked_days = 0

        # for date_attendance in get_attendance:
        #     if date_attendance.worked_hours > 0.0000000001:
        #         worked_days += 1

        days_num = self.env['hr.payslip'].search([('employee_id', '=', contract.employee_id['id']), ('date_from', '>=', str(date_from_converted)),
                                                  ('date_to', '<=', str(date_to_converted))])
        return days_num.num_of_days_comp

    def get_holiday_days(self, contract, payslip):
        date_from_converted = datetime.strptime(payslip.date_from, '%Y-%m-%d').date()
        date_to_converted = datetime.strptime(payslip.date_to, '%Y-%m-%d').date()
        get_attendance = self.env['hr.attendance'].search([('employee_id', '=', contract.employee_id['id']), ('check_in', '>=', str(date_from_converted)),
                                                           ('check_in', '<=', str(date_to_converted))])
        get_holidays = self.env['hr.attendance.holidays'].search([])
        get_holiday_setting = self.env['hr.holiday.setting'].search([])
        get_days_holiday = []
        get_day_reg = []
        call_holiday_setting = checking_holiday_setting(get_holiday_setting)
        for day_attendance in get_attendance:
            if day_attendance.worked_hours > 0.001:
                get_day_reg.append(datetime.strptime(day_attendance.check_in, '%Y-%m-%d %H:%M:%S').date())

        for holiday_setting in call_holiday_setting:
            for holiday_date in get_holidays:
                if holiday_setting == 1:
                    if datetime.strptime(holiday_date.holiday_start, '%Y-%m-%d  %H:%M:%S').date() >= date_from_converted and datetime.strptime(
                            holiday_date.holiday_start, '%Y-%m-%d  %H:%M:%S').date() <= date_to_converted and holiday_date.holiday_type == 'regular':
                        get_add = datetime.strptime(holiday_date.holiday_start, '%Y-%m-%d  %H:%M:%S').date() + timedelta(days=1)
                        get_days_holiday.append(get_add)
                elif holiday_setting == 2:
                    if datetime.strptime(holiday_date.holiday_start, '%Y-%m-%d  %H:%M:%S').date() >= date_from_converted and datetime.strptime(
                            holiday_date.holiday_start, '%Y-%m-%d  %H:%M:%S').date() <= date_to_converted and holiday_date.holiday_type == 'regular':
                        get_add = datetime.strptime(holiday_date.holiday_start, '%Y-%m-%d  %H:%M:%S').date()
                        get_days_holiday.append(get_add)
                elif holiday_setting == 3:
                    if datetime.strptime(holiday_date.holiday_start, '%Y-%m-%d  %H:%M:%S').date() >= date_from_converted and datetime.strptime(
                            holiday_date.holiday_start, '%Y-%m-%d  %H:%M:%S').date() <= date_to_converted and holiday_date.holiday_type == 'special':
                        get_add = datetime.strptime(holiday_date.holiday_start, '%Y-%m-%d  %H:%M:%S').date() + timedelta(days=1)
                        get_days_holiday.append(get_add)
                elif holiday_setting == 4:
                    if datetime.strptime(holiday_date.holiday_start, '%Y-%m-%d  %H:%M:%S').date() >= date_from_converted and datetime.strptime(
                            holiday_date.holiday_start, '%Y-%m-%d  %H:%M:%S').date() <= date_to_converted and holiday_date.holiday_type == 'special':
                        get_add = datetime.strptime(holiday_date.holiday_start, '%Y-%m-%d  %H:%M:%S').date()
                        get_days_holiday.append(get_add)
                else:
                    if datetime.strptime(holiday_date.holiday_start, '%Y-%m-%d  %H:%M:%S').date() >= date_from_converted and datetime.strptime(
                            holiday_date.holiday_start, '%Y-%m-%d  %H:%M:%S').date() <= date_to_converted and holiday_date.holiday_type == 'regular':
                        get_add = datetime.strptime(holiday_date.holiday_start, '%Y-%m-%d  %H:%M:%S').date()
                    if datetime.strptime(holiday_date.holiday_start, '%Y-%m-%d  %H:%M:%S').date() >= date_from_converted and datetime.strptime(
                            holiday_date.holiday_start, '%Y-%m-%d  %H:%M:%S').date() <= date_to_converted and holiday_date.holiday_type == 'special':
                        get_add = datetime.strptime(holiday_date.holiday_start, '%Y-%m-%d  %H:%M:%S').date()
                        get_days_holiday.append(get_add)

        get_intersection = intersection_list(get_day_reg, get_days_holiday)

        holiday_payment_no_attendance = 0

        for n in get_days_holiday:
            if n not in get_intersection:
                holiday_payment_no_attendance += 1

        return holiday_payment_no_attendance


# test model for salary rules
class InheritEmployee(models.Model):
    _inherit = 'hr.employee'
    @api.depends('attendance_ids')
    def _compute_last_attendance_id(self):
        for employee in self:
            if employee.attendance_ids:
                today_date = fields.Date.today()
                date_last = employee.attendance_ids and employee.attendance_ids[0] or False
                attendance_updated = []
                if datetime.strptime(today_date, '%Y-%m-%d').date() < datetime.strptime(date_last.check_in, '%Y-%m-%d %H:%M:%S').date():
                    
                    for x in employee.attendance_ids:
                        check_in_date = datetime.strptime(x.check_in, '%Y-%m-%d %H:%M:%S').date()
                        check_today = datetime.strptime(today_date, '%Y-%m-%d').date()
                        if check_in_date <= check_today:
                            attendance_updated.append(x)
                    sorted_date_lesser = sorted(attendance_updated, key=lambda x: x.check_in, reverse=True)
                    employee.last_attendance_id = sorted_date_lesser and sorted_date_lesser[0] or False
                else:
                    employee.last_attendance_id = employee.attendance_ids and employee.attendance_ids[0] or False
                    
    @api.constrains('check_in', 'check_out', 'employee_id')
    def _check_validity(self):
        """ Verifies the validity of the attendance record compared to the others from the same employee.
            For the same employee we must have :
                * maximum 1 "open" attendance record (without check_out)
                * no overlapping time slices with previous employee records
        """
        for attendance in self:
            # we take the latest attendance before our check_in time and check it doesn't overlap with ours
            last_attendance_before_check_in = self.env['hr.attendance'].search([
                ('employee_id', '=', attendance.employee_id.id),
                ('check_in', '<=', attendance.check_in),
                ('id', '!=', attendance.id),
            ], order='check_in desc', limit=1)
            if last_attendance_before_check_in and last_attendance_before_check_in.check_out and last_attendance_before_check_in.check_out > attendance.check_in:
                raise ValidationError(_("Cannot create new attendance record for %(empl_name)s, the employee was already checked in on %(datetime)s") % {
                    'empl_name': attendance.employee_id.name_related,
                    'datetime': fields.Datetime.to_string(fields.Datetime.context_timestamp(self, fields.Datetime.from_string(attendance.check_in))),
                })

            if not attendance.check_out:
                # if our attendance is "open" (no check_out), we verify there is no other "open" attendance
                no_check_out_attendances = self.env['hr.attendance'].search([
                    ('employee_id', '=', attendance.employee_id.id),
                    ('check_out', '=', False),
                    ('id', '!=', attendance.id),
                ], order='check_in desc', limit=1)
                if no_check_out_attendances:
                    raise ValidationError(_("Cannot create new attendance record for %(empl_name)s, the employee hasn't checked out since %(datetime)s") % {
                        'empl_name': attendance.employee_id.name_related,
                        'datetime': fields.Datetime.to_string(fields.Datetime.context_timestamp(self, fields.Datetime.from_string(no_check_out_attendances.check_in))),
                    })
            else:
                # we verify that the latest attendance with check_in time before our check_out time
                # is the same as the one before our check_in time computed before, otherwise it overlaps
                last_attendance_before_check_out = self.env['hr.attendance'].search([
                    ('employee_id', '=', attendance.employee_id.id),
                    ('check_in', '<', attendance.check_out),
                    ('id', '!=', attendance.id),
                ], order='check_in desc', limit=1)
                if last_attendance_before_check_out and last_attendance_before_check_in != last_attendance_before_check_out:
                    raise ValidationError(_("Cannot create new attendance record for %(empl_name)s, the employee was already checked in on %(datetime)s") % {
                        'empl_name': attendance.employee_id.name_related,
                        'datetime': fields.Datetime.to_string(fields.Datetime.context_timestamp(self, fields.Datetime.from_string(last_attendance_before_check_out.check_in))),
                    })
