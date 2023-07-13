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

    @api.multi
    def action_approve(self):
        # raise ValidationError(self.id)      

        if self.date_from and self.date_to:
            y = self.env['hr.attendance'].create({
                'employee_id': self.employee_id.id,
                'check_in': self.date_from,
                'check_out': self.date_to,
            })

            y.write({'leave_ids': [(4, self.id)]})

        x = super(LeavesAutomate, self).action_approve()
        return x


# class LeavesAutomate(models.Model):
#     _inherit = 'hr.holidays'

#     @api.multi
#     def automate_leaves(self):
#         leaves_dates = []
#         # check_list = []
#         # get_days_attendance = []
#         # get_attendance = self.env['hr.attendance'].search([('employee_id', '=', self.employee_id.id)])
#         date_from_converted = datetime.strptime(str(self.date_from), '%Y-%m-%d %H:%M:%S')
#         date_to_converted = datetime.strptime(str(self.date_to), '%Y-%m-%d %H:%M:%S')
#         # date_from_hour = datetime.strptime(str(self.date_from), '%Y-%m-%d %H:%M:%S').time()
#         get_leave_dates = get_dates_leaves(date_from_converted, date_to_converted)

#         for leave_days in get_leave_dates:
#             # leave_dates_combine = datetime.combine(leave_days.date(), date_from_hour)
#             # leaves_dates.append(leave_days.date())
#             if date.today() == leave_days:
#                 y = self.env['hr.attendance'].create({
#                     'employee_id': self.employee_id.id,
#                     'check_in': str(leave_days),
#                     'check_out': str(leave_days + timedelta(hours=8)),
#                 })
#                 y.write({'leave_ids': [(4, self.id)]})


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
# class TestModel(models.Model):
#     _inherit = 'hr.payslip'
