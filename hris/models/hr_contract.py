# -*- coding:utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import ValidationError
from datetime import datetime, timedelta
from odoo.tools.translate import _

from odoo.tools import float_round


class HRContract(models.Model):
    _inherit = 'hr.contract'

    def get_prev_ntp(self, contract, payslip):
        domain = [('date_release', '<', payslip.date_release),
                  ('contract_id', '=', contract.id),
                  ('contract_id.employee_id', '=', contract.employee_id.id)]
        prev_payslip = self.env['hr.payslip'].search(domain, limit=1, order="date_release DESC")
        if prev_payslip:
            datas = {'GTP': 0.0, 'EMPCTRB': 0.0, 'OtherTaxDed': 0.0}
            for line in prev_payslip.line_ids:
                if line.category_id.code in datas:
                    datas[line.category_id.code] += line.total
                else:
                    continue
            total_prev_ntp = datas['GTP'] - datas['EMPCTRB'] - datas['OtherTaxDed']
            if total_prev_ntp:
                return total_prev_ntp
        return 0.0

    """contributions"""

    def get_sss_ee(self, model, value, employee, code, basic_code):
        """Returns Second Cut off contribution of employee based on the first contribution"""
        max_contrib = self.env[model].search([], order='ss_ee DESC', limit=1)
        if max_contrib:
            payslip = self.env['hr.payslip'].search([('employee_id', '=', employee)], order='id DESC', limit=2)[1]
            if payslip:
                basic_val_last = payslip.line_ids.filtered(lambda r: r.code == basic_code)
                total_basic = basic_val_last.total + value
                cur = self.get_contrib_value_by_model(model, total_basic, args=[('effectivity_start', '<=', payslip.payroll_period_id.date_release), ('dummy_end_date', '>=', payslip.payroll_period_id.date_release)])
                current_contrib = cur.ss_ee
                sss_contrib = payslip.line_ids.filtered(lambda r: r.code == 'SSS-SM')
                total_contrib = current_contrib - sss_contrib.total
                if total_contrib > max_contrib.ss_ee:
                    second_contrib = sss_contrib.total - max_contrib.ss_ee
                    return abs(second_contrib)
                else:
                    return total_contrib
            else:
                return 0.0
        else:
            return 0.0

    def get_sss_er(self, model, value, employee, code):
        """Returns Second Cut off contribution of employer based on the first contribution"""
        max_contrib = self.env[model].search([], order='contrib_er DESC', limit=1)
        if max_contrib:
            payslip = self.env['hr.payslip'].search([('employee_id', '=', employee)], order='id DESC', limit=2)[1]
            if payslip:
                cur = self.get_contrib_value_by_model(model, value, args=[('effectivity_start', '<=', payslip.payroll_period_id.date_release), ('dummy_end_date', '>=', payslip.payroll_period_id.date_release)])
                current_contrib = cur.contrib_er
                sss_contrib = payslip.line_ids.filtered(lambda r: r.code == code)
                total_contrib = current_contrib + sss_contrib.total
                # print current_contrib,sss_contrib.total,max_contrib.contrib_er
                if total_contrib >= max_contrib.contrib_er:
                    second_contrib = max_contrib.contrib_er - sss_contrib.total
                    return abs(second_contrib)
                else:
                    return current_contrib
            else:
                return 0.0
        else:
            return 0.0

    def compute_phic(self, cutoff, value, employee, code, basic_code):
        if cutoff == 1 and value <= 10000.00:
            return 275 / 2.0
        elif cutoff == 1 and value > 10000.00:
            contrib = (value * 0.0275) / 2.0
            if contrib > 550:
                return 550
            else:
                return contrib
        else:
            payslip = self.env['hr.payslip'].search([('employee_id', '=', employee)], order='id DESC', limit=2)[1]
            if payslip:
                basic_value_last = payslip.line_ids.filtered(lambda r: r.code == basic_code)
                basic_val = basic_value_last.total
                phic_contrib = payslip.line_ids.filtered(lambda r: r.code == code)
                first_contrib = phic_contrib.total
                total_basic = value + basic_val
                val = ((value + basic_val) * 0.0275) / 2.0
                contrib = abs(first_contrib - val)
                if contrib >= 550:
                    return val
                else:
                    return contrib
            else:
                return 0.0

    def get_hdmf_contrib(self):
        """Returns HDMF Contribution of Employee"""
        if not self.hdmf_contrib_upgrade:
            return 100
        else:
            return self.hdmf_amount_upgraded

    @api.model
    def format_datetime(self, period, default_format='%d'):
        """Returns the day of date by default. """
        date = fields.Date.from_string(period).strftime(default_format)
        return date and int(date) or 0

    @api.model
    def get_column_value(self, model, value, args=[]):
        """Returns the column value of the selected record."""
        return self.get_contrib_value_by_model(model, value, args)

    @api.model
    def get_contrib_value_by_model(self, model, value, args=None):
        """Returns an instance of that record."""
        domain = [('min_range', '<=', value), ('max_range', '>=', value)]
        if args:
            domain += args
        return self.env[model].search(domain, limit=1)

    def _notify_manager(self, subject, body, follower_ids):
        """Allows to notify the manager of the employee."""
        self.message_post(body=body, subject=subject, partner_ids=[follower_ids])

    def make_message(self, notifications, date_permanent):
        """Create message body."""
        for manager, contracts in notifications.items():
            subject = _('List of Employee to be Reqularized')
            body = _("<strong>&emsp;Good Day!</strong><br /><br />")
            body += "<p>&emsp;This are the list of the employee(s) to be reqularized on %s</p>" % (date_permanent)
            body += "<ol>"
            for contract in contracts:
                body += "<li>%s</li>" % (contract.employee_id.name)
            body += "</ol>"
            follower_ids = manager.user_id.partner_id.id
            self._notify_manager(subject, body, follower_ids)
            contracts.reg_notif = True

        return True

    def set_notif(self, date_after_days):
        date_notif = date_after_days.strftime('%Y-%m-%d')
        date_notif_msg = date_after_days.strftime('%B %d, %Y')

        domain = [
            ('date_permanent', '=', date_notif),
            ('state', '=', 'open'),
            ('reg_notif', '=', False)]

        # domain = [
        #    ('date_permanent', '=', date_notif)]

        notifications = {}
        res = self.search(domain)
        for record in res.filtered(lambda r: r.employee_id.parent_id):
            if record.employee_id and record.employee_id.parent_id.id in notifications:
                notifications[record.employee_id.parent_id].append(record)
            else:
                notifications = {
                    record.employee_id.parent_id: record
                }

        self.make_message(notifications, date_notif_msg)

    def get_all_contracts(self):
        """Get all contracts to notify."""
        first_notif = self.env['ir.config_parameter'].get_param('en.notif.days', 'False')
        second_notif = self.env['ir.config_parameter'].get_param('en.notif2.days', 'False')

        if first_notif:
            number_of_days = self.env['ir.config_parameter'].get_param('reg.notif.days', 0)
            date_today = datetime.now()

            date_after_days = date_today + timedelta(days=float(number_of_days))
            self.set_notif(date_after_days)

        if second_notif:
            number_of_days = self.env['ir.config_parameter'].get_param('reg.notif2.days', 0)
            date_today = datetime.now()
            date_after_days = date_today + timedelta(days=float(number_of_days))
            self.set_notif(date_after_days)

    @api.onchange('trial_date_start')
    def onchange_date_trial_date(self):
        self.date_start = self.trial_date_start

    @api.onchange('job_title_move')
    def onchange_job_title_move(self):
        res = self.job_title_move.sorted(key=lambda r: r.date_start, reverse=True).mapped('job_id')
        self.job_id = res and res[0].id or False
        self.temp_job_id = res and res[0].id or False
        if res:
            self.struct_id = res and res[0].structure_id.id
            self.temp_struct_id = res and res[0].structure_id.id

    def get_wage(self, payslip, contract):
        if payslip.date_from < contract.new_salary_date and payslip.date_to >= contract.new_salary_date:
            wage = contract.old_wage
        elif payslip.date_from >= contract.new_salary_date:
            wage = contract.wage
        else:
            wage = contract.old_wage
        return wage

    # salarymovement on change
    @api.onchange('salary_move')
    def onchange_salary_move(self):

        new_date = False
        if self.salary_move:
            new_date = max(self.salary_move.mapped('date_start'))
        old_wage = self.salary_move.filtered(lambda l: l.date_end and l.date_end < new_date)
        self.new_salary_date = new_date

        res = self.salary_move.sorted(key=lambda r: r.date_start, reverse=True).mapped('amount')
        self.wage = res and res[0] or 0
        self.old_wage = len(res) > 1 and res[1] or 0
        self.temp_wage = res and res[0] or 0
        if self.salary_move and not self.avg_wrk_days_id:
            raise ValidationError(_("Please Enter Average Working Days!"))
        if self.temp_wage > 0:
            rate = self.wage / self.average_working_days
            location_id = self.employee_id.work_location_id.id
            if location_id:
                min_wage = self.env['hr.minimum_wage.earner'].is_minimum_wage(location_id, rate)

                if min_wage:
                    self.earner_type = 'mmw'
                else:
                    self.earner_type = 'nonmmw'
            else:
                self.earner_type = ''

    @api.depends('employee_id.work_location_id', 'salary_move')
    def _compute_min_wage(self):
        for record in self:
            res = record.salary_move.sorted(key=lambda r: r.date_start, reverse=True).mapped('amount')
            wage = res and res[0] or 0
            if wage > 0 and record.average_working_days > 0:
                rate = wage / record.average_working_days
                location_id = record.employee_id.work_location_id.id
                if location_id:
                    min_wage = self.env['hr.minimum_wage.earner'].is_minimum_wage(location_id, rate)

                    if min_wage:
                        record.earner_type = 'mmw'
                    else:
                        record.earner_type = 'nonmmw'
                else:
                    record.earner_type = ''

    def get_other_income(self, code, payslip, employee_id, contract_id):
        """Returns employee other income type amount."""

        other_income = self.env['hr.employee.other_income'].search(
            [('code', '=', code),
             ('contract_id', '=', contract_id.id),
             ('contract_id.employee_id', '=', employee_id.id),
             ('date_start', '<=', payslip.date_to),
             ('date_end_modify', '>=', payslip.date_from)])
        res = self.env['hr.employee.other_income'].read_group(
            [('id', 'in', other_income.ids)], ['code', 'amount'], ['code'])
        total_amount = dict((data['code'], data['amount']) for data in res)

        return total_amount.get(code, 0)

    def get_other_deduction(self, code, payslip, employee_id, contract_id):
        """Returns employee other deduction type amount."""
        other_deduction = self.env['hr.employee.other_deduction'].search(
            [('code', '=', code),
             ('contract_id', '=', contract_id.id),
             ('contract_id.employee_id', '=', employee_id.id),
             ('date_start', '<=', payslip.date_to),
             ('date_end_modify', '>=', payslip.date_from)])

        res = self.env['hr.employee.other_deduction'].read_group(
            [('id', 'in', other_deduction.ids)], ['code', 'amount'], ['code'])

        total_amount = dict((data['code'], data['amount']) for data in res)

        return total_amount.get(code, 0)

    def newly_hired_salary(self, payslip, employee_id, contract_id, days=0):
        """Return employee prorated wage if date hired fall on the middle of the cutoff"""
        wage = contract_id.wage
        if contract_id.schedule_pay == 'bi-weekly':
            wage /= 2.0

        if payslip.date_from == contract_id.date_start:
            return wage

        if days > 0:
            return (contract_id.wage / contract_id.average_working_days) * days

        return wage

    def prorate_salary(self, payslip, employee_id, contract_id, days=0):
        """Computes employee prorated salary.
           Days: Work hours or Absent hours
           Absent: Usually deducted from basic pay on the salary computation
         """
        date_from = fields.Date.from_string(payslip.date_from)
        date_to = fields.Date.from_string(payslip.date_to)
        date_hired = fields.Date.from_string(contract_id.date_start)

        # if date hired fall on the cut-off
        if date_from <= date_hired <= date_to:
            return self.newly_hired_salary(payslip, employee_id, contract_id, days)

        old_rate = [('date_start', '<=', payslip.date_to),
                    ('date_end', '>=', payslip.date_from),
                    ('contract_id', '=', contract_id.id),
                    ('contract_id.employee_id', '=', employee_id.id)
                    ]

        old_salary_movements = self.env['hr.salary.move'].search(old_rate)
        old_rate_payout = sum(old_salary_movements.mapped('amount'))
        old_daily_rate = sum(old_salary_movements.mapped('daily_rate'))
        if contract_id.schedule_pay == 'bi-weekly':
            old_rate_payout /= 2.0

        new_rate = [('date_end', '=', False),
                    ('contract_id', '=', contract_id.id),
                    ('contract_id.employee_id', '=', employee_id.id)
                    ]

        new_salary_movements = self.env['hr.salary.move'].search(new_rate)
        new_daily_rate = sum(new_salary_movements.mapped('daily_rate'))

        new_old_rate = new_daily_rate - old_daily_rate

        # check if salary needs to be prorated based on movements covered
        salary_movements = old_salary_movements | new_salary_movements
        if len(salary_movements) == 1:
            wage = contract_id.wage
            if contract_id.schedule_pay == 'bi-weekly':
                wage /= 2.0
            return wage

        # attendances
        attendances = {
            'name': _("Regular Work"),
            'sequence': 1,
            'code': 'RegWrk',
            'number_of_days': 0.0,
            'number_of_hours': 0.0,
            'contract_id': contract_id.id,
        }

        effective_date = new_salary_movements.mapped('date_start')
        effective_date = effective_date and effective_date[0]

        worked_hours = self.env['hr.payslip'].get_worked_hour_lines(employee_id.id,
                                                                    effective_date, payslip.date_to)
        for record in worked_hours:

            attendances['number_of_days'] += float_round(record.worked_hours / 8.0, precision_digits=2)
            attendances['number_of_hours'] += float_round(record.worked_hours, precision_digits=2)

        number_of_days = attendances.get('number_of_days')

        diff_amount = number_of_days * new_old_rate
        prorated_payout = old_rate_payout + diff_amount

        return prorated_payout

    @api.model
    def create(self, vals):
        if 'temp_wage' in vals:
            vals['wage'] = vals.get('temp_wage')

        if 'temp_job_id' in vals:
            vals['job_id'] = vals.get('temp_job_id')

        if 'temp_struct_id' in vals:
            vals['struct_id'] = vals.get('temp_struct_id')

        if 'avg_wrk_days_id' in vals:
            awd = self.env['hr.avg_wrk_days.config'].browse(vals.get('avg_wrk_days_id'))
            vals['average_working_days'] = awd.name

        res = super(HRContract, self).create(vals)
        if res.employee_id and res.employee_id.auto_generate_barcode:
            code = self.env['ir.sequence'].next_by_code('hr.employee')
            prefix = datetime.strptime(res.date_start, '%Y-%m-%d')
            prefix2 = prefix.strftime('%m%y')
            res.employee_id.barcode = prefix2 + code
        res.onchange_emp_status()
        return res

    @api.multi
    def write(self, vals):

        if 'temp_wage' in vals:
            vals['wage'] = vals.get('temp_wage')

        if 'temp_job_id' in vals:
            vals['job_id'] = vals.get('temp_job_id')

        if 'temp_struct_id' in vals:
            vals['struct_id'] = vals.get('temp_struct_id')

        if 'avg_wrk_days_id' in vals:
            awd = self.env['hr.avg_wrk_days.config'].browse(vals.get('avg_wrk_days_id'))
            vals['average_working_days'] = awd.name

        res = super(HRContract, self).write(vals)
        self.onchange_emp_status()
        return res

    @api.constrains('employee_id')
    def check_duplicate_contract(self):
        for rec in self:
            contracts = self.env['hr.contract'].search([('employee_id', '=', rec.employee_id.id), ('id', '!=', rec.id)])
            if contracts:
                raise ValidationError(_("You cannot create multiple contract for same employee!"))

    @api.onchange('avg_wrk_days_id')
    def onchange_awd(self):
        for rec in self:
            if rec.avg_wrk_days_id:
                rec.average_working_days = rec.avg_wrk_days_id.name
                salary_move = rec.salary_move and rec.salary_move[0]
                if salary_move:  # Check if salary_move exists
                    # Assuming 'average_working_days' is a field in 'hr.salary.move'
                    salary_move.write({'average_working_days': rec.average_working_days})

    # Insert data when importing from the csv file 
    @api.depends('average_working_days', 'wage', 'new_salary_date', 'salary_move', 'temp_wage')
    def _compute_awd(self):
        for record in self:
            if record.average_working_days > 0:
                salary_move = record.salary_move and record.salary_move[0]
                if salary_move:
                    existing_avg_wrk_days = self.env['hr.avg_wrk_days.config'].search([
                        ('name', '=', record.average_working_days)
                    ], limit=1)
                    if existing_avg_wrk_days:
                        record.avg_wrk_days_id = existing_avg_wrk_days
                        record.wage = salary_move.amount
                        record.new_salary_date = salary_move.date_start
                    else:
                        new_avg_wrk_days = self.env['hr.avg_wrk_days.config'].create({
                            'name': record.average_working_days,
                        })
                        record.avg_wrk_days_id = new_avg_wrk_days
                        record.wage = salary_move.amount
                        record.new_salary_date = salary_move.date_start

    def onchange_emp_status(self):
        if self.resigned and self.employee_id:
            self.employee_id.active = False
            self.env['hr.employee.clearance'].\
                create({
                    'employee_id': self.employee_id.id,
                    'cleareance_date': fields.Date.today()
                })
            if self.state != 'close':
                self.state = 'close'
            self.employee_id.relived()

    @api.onchange('job_id', 'job_title_move')
    def change_job_id(self):
        if self.job_id:
            self.employee_id.write({'job_id': self.job_id.id})

    def get_old_new_salary_date(self, payslip):
        old_salary_date = self.salary_move.filtered(lambda r: r.date_start and r.date_end and r.date_start <= payslip.date_from <= r.date_end)
        new_salary_date = self.salary_move.filtered(lambda r: r.date_start <= payslip.date_to and r.date_end == False)
        if old_salary_date and new_salary_date:
            return {'old_salary_date': old_salary_date,
                    'new_salary_date': new_salary_date}

    def get_week_days(self):
        working_days = self.env['hr.employee.schedule.work_time'].search([('employee_id', '=', self.employee_id.id)])
        return working_days.work_time_lines.mapped('days_of_week')

    def is_holiday(self, week_days, rec):
        holidays = self.env['hr.attendance.holidays'].search([])
        if rec.strftime('%A').lower() in week_days:
            if not holidays.filtered(lambda l: fields.Date.from_string(l.holiday_start) <= rec <= fields.Date.from_string(l.holiday_end)):
                return 1
        else:
            return 0

    def salary_adjustment_earnings(self, payslip):
        old_new_salary_date = self.get_old_new_salary_date(payslip)
        if not old_new_salary_date:
            return 0
        old_salary = old_new_salary_date['old_salary_date'].amount / 2
        new_salary = old_new_salary_date['new_salary_date'].amount / 2
        earnings = new_salary - old_salary
        return earnings

    def salary_adjustment_deductions(self, payslip):
        old_new_salary_date = self.get_old_new_salary_date(payslip)
        if not old_new_salary_date:
            return 0
        old_date = old_new_salary_date['old_salary_date']
        new_date = old_new_salary_date['new_salary_date']
        leaves = self.env['hr.holidays'].search([('state', 'in', ['validate', 'validate1']),
                                                ('date_approved', '>=', payslip.date_from),
                                                ('date_approved', '<=', payslip.date_to),
                                                ('type', '=', 'remove')])
        absent_attendance = self.env['hr.attendance'].search([('check_in', '>=', payslip.contract_id.new_salary_date),
                                                              ('check_out', '<=', payslip.date_to),
                                                              ('absent_hours', '>', 0),
                                                              ('employee_id', '=', payslip.employee_id)])
        count = 0
        deduction = 0
        week_days = self.get_week_days()
        for leave in leaves:
            leave_start_date = fields.Date.from_string(leave.date_from)
            leave_end_date = fields.Date.from_string(leave.date_to)
            if leave_end_date and leave_start_date:
                dates = [leave_start_date + timedelta(days=i) for i in range((leave_end_date - leave_start_date).days + 1)]
                for rec in dates:
                    if rec >= fields.Date.from_string(new_date.date_start):
                        count += self.is_holiday(week_days, rec)
            deduction = (count * new_date.daily_rate) - (count * old_date.daily_rate)
        absent_hours = absent_attendance and sum(absent_attendance.mapped('absent_hours')) or 0
        deduction += (absent_hours * new_date.hourly_rate) - (absent_hours * old_date.hourly_rate)
        return deduction

    reg_notif = fields.Boolean('Notified', help="Notified by Regularization.")
    other_income_line = fields.One2many('hr.employee.other_income', 'contract_id', 'Other Income')
    other_deduction_line = fields.One2many('hr.employee.other_deduction', 'contract_id', 'Other Deduction')
    date_permanent = fields.Date('Date Permanent', track_visibility='onchange')
    resigned = fields.Boolean('Resigned')
    contract_type = fields.Selection([('probationary', 'With Contract'),
                                      ('regular', 'Regular')], 'Type',
                                     related="type_id.contract_type", required=True)
    earner_type = fields.Selection([('mmw', 'Minimum Wage Earner'),
                                    ('nonmmw', 'Non Minimum Wage Earner')],
                                   'Earner Type', compute='_compute_min_wage', store=True)
    job_title_move = fields.One2many('hr.job.move', 'contract_id', 'Job Title Movement')
    salary_move = fields.One2many('hr.salary.move', 'contract_id', 'Salary Movement')
    average_working_days = fields.Float('Average Working Days')
    avg_wrk_days_id = fields.Many2one('hr.avg_wrk_days.config', string="Average Working Days")
    # temporary storage for onchange values
    hdmf_contrib_upgrade = fields.Boolean(string="HDMF Upgrade")
    hdmf_amount_upgraded = fields.Float(string="HDMF Amount")
    old_wage = fields.Float("Old Salary")
    new_salary_date = fields.Date('New Salary Date', compute="_compute_awd")
    temp_wage = fields.Float('Wage', compute="_compute_awd")
    temp_job_id = fields.Many2one('hr.job', 'Job')
    temp_struct_id = fields.Many2one('hr.payroll.structure', 'Salary Structure')


class HRContractType(models.Model):
    _inherit = 'hr.contract.type'

    contract_type = fields.Selection([('probationary', 'With Contract'),
                                      ('regular', 'Regular')], 'Type', default='probationary', required=True)


class HRJobMove(models.Model):
    _name = 'hr.job.move'
    _description = 'Job Title Movement'
    _order = 'date_start desc'

    @api.constrains('date_start', 'date_end')
    def check_date(self):
        if self.filtered(lambda r: r.date_end and r.date_end < r.date_start):
            raise ValidationError(_("End date 'date' must be greater than start date 'date'!"))

    job_id = fields.Many2one('hr.job', 'Job Title', required=True)
    date_start = fields.Date('Date Start', required=True)
    date_end = fields.Date('Date End')
    contract_id = fields.Many2one('hr.contract', 'Contract')


class HRSalaryMove(models.Model):
    _name = 'hr.salary.move'
    _description = 'Salary Movement'
    _order = 'date_start desc'

    @api.constrains('date_start', 'date_end')
    def check_date(self):
        if self.filtered(lambda r: r.date_end and r.date_end < r.date_start):
            raise ValidationError(_("End date 'date' must be greater than start date 'date'!"))

    @api.depends('amount', 'average_working_days')
    def _compute_rate(self):
        for record in self:
            if record.contract_id.average_working_days > 0:
                record.daily_rate = record.amount / record.contract_id.average_working_days
                record.hourly_rate = record.daily_rate / 8.0

    @api.constrains('date_start', 'date_end')
    def _check_validity(self):
        """Check validities of salary movement,with no end date , overlapped date."""
        for record in self:
            # Check if there is previous salary movement with no end date.
            # Should be created  before doing new movement
            count = self.search_count([
                ('contract_id', '=', record.contract_id.id),
                ('date_end', '=', False)])

            if count > 1:
                raise ValidationError(_("Previous employee salary movement must have end date before creating new salary movement."))

            # Check if there is an overlap with previous salary movement.
            salary_movement = self.search([('id', '!=', record.id),
                                           ('contract_id', '=', record.contract_id.id),
                                           ('date_end', '>', record.date_start)],
                                          order='date_start desc', limit=1)

            if salary_movement:
                raise ValidationError(_("Employee salary movement has overlap with %s %s ") % (salary_movement.date_start, salary_movement.date_end))

    @api.constrains('date_start')
    def _check_lockout_period(self):
        for salary in self:
            IrConfigParameter = self.env['ir.config_parameter']
            lockout = IrConfigParameter.get_param('salary.movement.lockout')
            lockout_period = float(IrConfigParameter.get_param('salary.movement.lockout_period'))

            if not lockout:
                continue

            today = fields.Date.today()
            date_from = fields.Date.to_string(fields.Datetime.context_timestamp(self, fields.Datetime.from_string(salary.date_start)))

            domain = [
                ('start_date', '<=', today),
                ('end_date', '>=', date_from)
            ]

            cutoffs = self.env['hr.payroll.period_line'].search_count(domain)

            if cutoffs > lockout_period:
                raise ValidationError(_('Unable to add or modify salary.The lockout period has been reached!'))

    amount = fields.Float('Amount', required=True)
    date_start = fields.Date('Date Start', required=True)
    date_end = fields.Date('Date End')
    contract_id = fields.Many2one('hr.contract', 'Contract')
    average_working_days = fields.Float('Average Working Days')
    daily_rate = fields.Float('Daily Rate', compute='_compute_rate')
    hourly_rate = fields.Float('Hourly Rate', compute='_compute_rate')


class HREmployeeOtherIncome(models.Model):
    _name = 'hr.employee.other_income'
    _description = 'Employee Other Income'

    @api.onchange('rule_id')
    def onchange_rule(self):
        if self.rule_id:
            self.code = self.rule_id.code
            self.name = self.rule_id.name

    @api.constrains('date_start', 'date_end')
    def _check_validity_start_end_date(self):
        """Verifies if start date is earlier than end date period. """
        for other_income in self:
            if other_income.date_start and other_income.date_end:
                if other_income.date_end < other_income.date_start:
                    raise ValidationError(_('"End date" date cannot be earlier than "Start date" date.'))

    # @api.constrains('code', 'other_income_type')
    # def check_other_income_code(self):
    #    """Check duplicated code for other income type."""
    #    for record in self:
    #        if record.code and record.other_income_type:
    #            count = self.search_count([('id', '!=' ,record.id),
    #                                       ('code', '=', record.code),
    #                                       ('other_income_type', '!=', record.other_income_type)])
    #            if count > 0:
    #                raise ValidationError(_('The code is already defined for this type'))

    code = fields.Char('Code', required=True, size=32)
    name = fields.Char('Name', required=True, size=32)
    other_income_type = fields.Selection([('tx', 'Taxable'),
                                          ('ntx', 'Non-Taxable')], 'Type')

    amount = fields.Float('Amount', required=True, default=0)
    date_start = fields.Date('Start Date', required=True)
    date_end = fields.Date('End Date')
    contract_id = fields.Many2one('hr.contract', 'Contract', ondelete='cascade')
    rule_id = fields.Many2one('hr.salary.rule', 'Name')
    rule_categ_id = fields.Many2one('hr.salary.rule.category', 'Category')
    recurring = fields.Boolean(compute='get_recurring_method', string="Recurring")
    date_end_modify = fields.Date(compute='get_recurring_method', store=True)

    @api.depends('date_end', 'date_start')
    def get_recurring_method(self):
        for res in self:
            if res.date_start:
                if not res.date_end:
                    res.recurring = True
                    dt = datetime.strptime(res.date_start, "%Y-%m-%d")
                    res.date_end_modify = dt.replace(year=2130, )
                else:
                    res.recurring = False
                    res.date_end_modify = res.date_end

    def update_end_date(self):
        for res in self.env['hr.employee.other_income'].search([]):
            if res.date_start:
                if not res.date_end:
                    res.recurring = True
                    dt = datetime.strptime(res.date_start, "%Y-%m-%d")
                    res.date_end_modify = dt.replace(year=2130, )
                else:
                    res.recurring = False
                    res.date_end_modify = res.date_end


class HREmployeeOtherDeduction(models.Model):
    _name = 'hr.employee.other_deduction'
    _description = 'Employee Other Deduction'

    def update_end_date(self):
        payslip = self.env['hr.payslip'].search([])
        for slip in payslip:
            slip.action_compute_attendance()

        for res in self.env['hr.employee.other_deduction'].search([]):
            if res.date_start:
                if not res.date_end:
                    res.recurring = True
                    dt = datetime.strptime(res.date_start, "%Y-%m-%d")
                    res.date_end_modify = dt.replace(year=2130, )
                else:
                    res.recurring = False
                    res.date_end_modify = res.date_end

    @api.onchange('rule_id')
    def onchange_rule(self):
        if self.rule_id:
            self.code = self.rule_id.code
            self.name = self.rule_id.name

    @api.constrains('date_start', 'date_end')
    def _check_validity_start_end_date(self):
        """Verifies if start date is earlier than end date period. """
        for other_deduction in self:
            if other_deduction.date_start and other_deduction.date_end:
                if other_deduction.date_end < other_deduction.date_start:
                    raise ValidationError(_('"End date" date cannot be earlier than "Start date" date.'))

    # @api.constrains('code', 'other_deduction_type')
    # def check_other_deduction_code(self):
    #    """Check duplicated code for other deduction type."""
    #    for record in self:
    #        if record.code and record.other_deduction_type:
    #            count = self.search_count([('id', '!=' ,record.id),
    #                                       ('code', '=', record.code),
    #                                       ('other_deduction_type', '!=', record.other_deduction_type)])
    #            if count > 0:
    #                raise ValidationError(_('The code is already defined for this type'))

    code = fields.Char('Code', required=True, size=32)
    name = fields.Char('Name', required=True, size=32)
    other_deduction_type = fields.Selection([('tx', 'Taxable'),
                                             ('ntx', 'Non-Taxable')], 'Type')

    amount = fields.Float('Amount', required=True, default=0)
    date_start = fields.Date('Start Date', required=True)
    date_end = fields.Date('End Date')
    contract_id = fields.Many2one('hr.contract', 'Contract', ondelete='cascade')
    rule_id = fields.Many2one('hr.salary.rule', 'Name')
    rule_categ_id = fields.Many2one('hr.salary.rule.category', 'Category')
    recurring = fields.Boolean(compute='get_recurring_method', string="Recurring")
    date_end_modify = fields.Date(compute='get_recurring_method', store=True)

    @api.depends('date_end', 'date_start')
    def get_recurring_method(self):
        for res in self:
            if res.date_start:
                if not res.date_end:
                    res.recurring = True
                    dt = datetime.strptime(res.date_start, "%Y-%m-%d")
                    res.date_end_modify = dt.replace(year=2130, )
                else:
                    res.recurring = False
                    res.date_end_modify = res.date_end


class HRAverageWorkingDays(models.Model):
    _name = 'hr.avg_wrk_days.config'
    _description = 'HR Average Working Days'

    name = fields.Float(string="Average Working Days")


class HRMinimumWageEarner(models.Model):
    _name = 'hr.minimum_wage.earner'
    _description = 'HR Minimum Wage Earner'

    def is_minimum_wage(self, location_id, rate):
        """Check if minimum wage earner."""
        return self.search([('job_location_id', '=', location_id), ('min_rate', '>=', rate)])

    job_location_id = fields.Many2one('hr.employee.work_location', 'Job Location', required=True)
    min_rate = fields.Float('Minimum Rate')
