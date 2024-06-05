from odoo import fields, models, api, _
from datetime import date, datetime, timedelta
from dateutil import relativedelta
from odoo.exceptions import UserError, ValidationError
import logging
_logger = logging.getLogger(__name__)

HOURS_PER_DAY = 8

class LeaveBenefitsPolicy(models.Model):
    _name = 'leave.benefits.policy'
    _description = 'leave benefits policy managements'

    length_of_service = fields.Integer('Length of Service (Month)')
    base_leave = fields.Integer('Base Leave (Priority)')
    equivalent_vl_credits = fields.Integer('Equivalent VL credits')
    equivalent_sl_credits = fields.Integer('Equivalent SL credits')


class HrLeaveType(models.Model):
    _inherit = 'hr.leave.type'

    def count_amount_for_days(self, record, employee, amount):
        if record.holiday_status_id.leave_type_selection in ['VL', 'SL']:
            current_date = date.today().strftime("%Y-%m-%d")
            start_date = datetime.strptime(employee.contract_id.date_permanent, "%Y-%m-%d")
            end_date = datetime.strptime(current_date, "%Y-%m-%d")
            delta = relativedelta.relativedelta(end_date, start_date)
            leave_benefits_policy = self.env['leave.benefits.policy'].search(
                [('length_of_service', '=', int(delta.years))],
                limit=1)
            if int(delta.years) > 5:
                leave_benefits_policy = self.env['leave.benefits.policy'].search(
                    [('length_of_service', '=', 5)])
            if int(delta.months) < 5:
                return 0
            if record.holiday_status_id.leave_type_selection in ['VL', 'SL']:
                amount += leave_benefits_policy.base_leave
            return amount
        return amount

    def cron_create_leaves(self):
        today = datetime.now()
        if today.day == 1:
            for emp in [rec.employee_id for rec in self.env['hr.contract'].search([
                ('type_id', '=', 'REGULAR'),
                ('state', '=', 'draft'),
                ])]:
                emp.allocate_base_leave(emp)
                datetime_str = emp.contract_id.date_start
                datetime_object = datetime.strptime(datetime_str, '%Y-%m-%d')
                if datetime_object.day == 1 and datetime_object.month == 1:
                    emp.allocate_benefit_leave(emp)
        else:
            if today.day == 31 and today.month == 12:
                allocated_recs = self.env['hr.holidays'].search(
                    [('holiday_type', '=', 'employee'), ('holiday_status_id.limit', '=', False),
                     ('state', '=', 'validate'),('holiday_status_id.leave_type_selection', 'in', ['SL','VL'])])
                for old_leave in allocated_recs:
                    try:
                        old_leave.action_refuse_allocation()
                    except UserError:
                        pass
                    old_leave.action_draft()
                    # old_leave.unlink()
            # experience_emp = self.env['hr.contract'].search([]).filtered(lambda y: int(y.date_start[:4]) <= today.year - 2)
            # experience_dates = [(int(x.date_start[-2:]), int(x.date_start[5:7])) for x in experience_emp]
            # if (today.day, today.month) in experience_dates:
            #     contract_ids = self.env['hr.contract'].search([]).filtered(
            #         lambda l: (int(l.date_start[-2:]) == today.day and int(l.date_start[5:7]) == today.month))
            #     [x.employee_id.allocate_benefit_leave(x.employee_id) for x in contract_ids]
            # permanent_date_today = self.env['hr.contract'].search([('date_permanent', '=', today.date())])
            # if permanent_date_today:
            #     [x.employee_id.allocate_base_leave(x.employee_id) for x in permanent_date_today]


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    def allocate_base_leave(self, emp):
        today = datetime.now()
        start_date = datetime.strptime(emp.contract_id.date_start, '%Y-%m-%d')
        experience_months = (today.year - start_date.year) * 12 + (today.month - start_date.month)
        vl_amount = self.env['hr.leave.type'].search(
            [('state', '=', 'activate'), ('holiday_status_id.leave_type_selection', '=', 'VL')], limit=1).amount
        sl_amount = self.env['hr.leave.type'].search(
            [('state', '=', 'activate'), ('holiday_status_id.leave_type_selection', '=', 'SL')], limit=1).amount
        lbp_rec = self.env['leave.benefits.policy'].search([('length_of_service', '=', int(experience_months))])
        if lbp_rec:
            benefit_amount = lbp_rec.base_leave / 2
        else:
            max_length_of_service = max([x.length_of_service for x in self.env['leave.benefits.policy'].search([])])
            max_rec = self.env['leave.benefits.policy'].search([('length_of_service', '=', max_length_of_service)])
            benefit_amount = (max_rec.base_leave / 2) + max_rec.equivalent_sl_credits
        if experience_months < 6:
            benefit_amount = 0
        emp.create_leave_sp(emp, vl_amount + benefit_amount, sl_amount + benefit_amount)

    def allocate_benefit_leave(self, emp):
        today = datetime.now()
        datetime_str = emp.contract_id.date_start
        datetime_object = datetime.strptime(datetime_str, '%Y-%m-%d')
        experience_months = (today.year - datetime_object.year) * 12 + (today.month - datetime_object.month)
        lbp_rec = self.env['leave.benefits.policy'].search([('length_of_service', '=', int(experience_months))])
        if lbp_rec and experience_months >= 12:
            vl_amount = lbp_rec.equivalent_vl_credits
            sl_amount = lbp_rec.equivalent_sl_credits
            emp.create_leave_sp(emp, vl_amount, sl_amount)


    def create_leave_sp(self, emp, vl_amount, sl_amount):
        holidays = self.env['hr.holidays']
        record_vl = self.env['hr.leave.type'].search(
            [('state', '=', 'activate'), ('holiday_status_id.leave_type_selection', '=', 'VL')], limit=1)
        record_sl = self.env['hr.leave.type'].search(
            [('state', '=', 'activate'), ('holiday_status_id.leave_type_selection', '=', 'SL')], limit=1)
        vl = self.prepare_value(record_vl, emp, vl_amount)
        leaves_vl = holidays.create(vl)
        leaves_vl.action_validate()
        sl = self.prepare_value(record_sl, emp, sl_amount)
        leaves_sl = holidays.create(sl)
        leaves_sl.action_validate()

    def prepare_value(self, record, emp, amount):
        name = record.holiday_status_id.name + ' - ' + datetime.now().strftime('%B').upper() + ' ' + str(datetime.now().year)
        values = {
            'name': record.holiday_status_id and record.holiday_status_id.name,
            'type': 'add',
            'holiday_type': 'category',
            'holiday_status_id': record.holiday_status_id.id,
            'number_of_days_temp': 0,
            'employee_id': False
        }
        if 'category' in ('converted', 'forfeited', 'less_carry', 'expired'):
            values['type'] = 'remove'
        else:
            values['type'] = 'add'
            
        values['name'] = name
        values['holiday_type'] = 'employee'
        values['category_id'] = False
        values['employee_id'] = emp.id
        values['process_type'] = 'earning'
        values['date_processed'] = fields.Datetime.now()
        values['number_of_days_temp'] = amount
        values['year'] = datetime.today().year
        return values