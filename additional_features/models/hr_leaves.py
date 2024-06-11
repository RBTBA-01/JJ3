from odoo import fields, models, api
import pytz
from odoo.tools.translate import _
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT,DEFAULT_SERVER_DATE_FORMAT
from datetime import date, datetime, timedelta,time
from dateutil.relativedelta import relativedelta
import collections, functools, operator
from odoo.tools import float_compare
import calendar
from odoo.exceptions import UserError, AccessError, ValidationError

month_list = [('01', 'January'), ('02', 'February'), ('03', 'March'), ('04', 'April'),
              ('05', 'May'), ('06', 'June'), ('07', 'July'), ('08','August'),
              ('09', 'September'), ('10', 'October'), ('11', 'November'), ('12', 'December'), ]

class HolidaysType(models.Model):

    _inherit = "hr.holidays.status"

    @api.multi
    def get_days(self, employee_id):
        result = dict(
            (id, dict(max_leaves=0, leaves_taken=0, remaining_leaves=0, virtual_remaining_leaves=0)) for id in self.ids)

        holidays = self.env['hr.holidays'].search([
            ('employee_id', '=', employee_id),
            ('state', 'in', ['confirm', 'validate1', 'validate']),
            ('holiday_status_id', 'in', self.ids),
            ('expired_leave','=',False),
        ])
        for holiday in holidays:
            status_dict = result[holiday.holiday_status_id.id]
            if holiday.type == 'add':
                if holiday.state == 'validate':
                    status_dict['virtual_remaining_leaves'] += holiday.number_of_days_temp
                    status_dict['max_leaves'] += holiday.number_of_days_temp
                    status_dict['remaining_leaves'] += holiday.number_of_days_temp
            elif holiday.type == 'remove':  # number of days is negative
                status_dict['virtual_remaining_leaves'] -= holiday.number_of_days_temp
                if holiday.state == 'validate':
                    status_dict['leaves_taken'] += holiday.number_of_days_temp
                    status_dict['remaining_leaves'] -= holiday.number_of_days_temp
        return result


class HrHolidaysExtended(models.Model):
    _inherit = 'hr.holidays'
    _order = 'date_from DESC'

    active = fields.Boolean(string="Active", default=True)
    expired_leave = fields.Boolean()
    expiration_date = fields.Date()
    curr_expiration_date = fields.Date('Current Year Expiration',compute='get_carry_over_details')
    curr_date_leave_count = fields.Float(compute='get_carry_over_details')

    carry_over_id = fields.Many2one('leave.carry.over','Carry Over Count',compute='get_carry_over_details')
    carry_over_expiration = fields.Date("Expiration Date",related='carry_over_id.carry_over_expiration')
    number_of_carry_over = fields.Float('For Carry Over')
    is_carry_over = fields.Boolean(compute='get_carry_over_details',store=True)
    year = fields.Integer()
    show_leave_credit = fields.Boolean(compute='get_carry_over_details')
    total_to_deduct = fields.Float()

    leave_approve_date = fields.Datetime("Date Approved")

    @api.multi
    def action_approve(self):
        """Check notice period and lockout period before approving."""
        res = super(HrHolidaysExtended, self).action_approve()
        self.leave_approve_date = fields.Datetime.now()

        for record in self:
            carry_over_line = self.env['leave.carry.over.line']
            existing_carry_over_line = self.env['leave.carry.over.line'].search(
                [('carry_over_id', '=', record.carry_over_id.id), ('leave_id', '=', record.id),
                 ('year', '=', record.year)], limit=1)
            if not existing_carry_over_line:
                carry_over_line.create({'carry_over_id': record.carry_over_id.id, 'leave_id': record.id, 'amount': max(0.0, record.number_of_days_temp), 'year': record.year})
            else:
                existing_carry_over_line.update({'amount': max(0.0, record.number_of_days_temp)})
            if record.holiday_status_id:
                record.curr_expiration_date = record.holiday_status_id.expiration_date
                record.curr_date_leave_count = record.holiday_status_id.virtual_remaining_leaves
        return res

    @api.multi
    def action_refuse(self):
        res = super(HrHolidaysExtended, self).action_refuse()
        for record in self:
            existing_carry_over_line = self.env['leave.carry.over.line'].search(
                [('carry_over_id', '=', record.carry_over_id.id), ('leave_id', '=', record.id), ('year', '=', record.year)],
                limit=1)
            if existing_carry_over_line:
                existing_carry_over_line.unlink()
        return res

    @api.onchange('holiday_status_id','employee_id','date_from','number_of_days_temp','state','date_to')
    def onchange_total_to_deduct(self):
        if self.date_from:
            carry_over_object = self.env['leave.carry.over'].search( [('holiday_status_id', '=', self.holiday_status_id.id), ('employee_id', '=', self.employee_id.id),('year', '=', self.year)], limit=1)
            if carry_over_object.carry_over_expiration:
                carry_over = carry_over_object.filtered( lambda x: x.carry_over_expiration >= datetime.strptime(self.date_from,  DEFAULT_SERVER_DATETIME_FORMAT).strftime( '%Y-%m-%d'))
            else:
                carry_over = carry_over_object
            leave_type = self.env['hr.leave.type'].search([('holiday_status_id', '=', self.holiday_status_id.id)],  limit=1)
            if leave_type and carry_over:
                self.number_of_carry_over  =  max(0, min(self.number_of_days_temp,carry_over.amount))

    @api.depends('holiday_status_id','employee_id','date_from','number_of_carry_over')
    def get_carry_over_details(self):
        for record in self:
            if record.date_from and record.holiday_status_id:
                carry_over_object= self.env['leave.carry.over'].search([('holiday_status_id','=',record.holiday_status_id.id),('employee_id','=',record.employee_id.id),('year','=',record.year)],limit=1)
                if carry_over_object.carry_over_expiration:
                    carry_over = carry_over_object.filtered(lambda x: x.carry_over_expiration > datetime.strptime(record.date_from,   DEFAULT_SERVER_DATETIME_FORMAT).strftime('%Y-%m-%d'))
                else:
                    carry_over = carry_over_object
                if carry_over:
                    record.carry_over_id = carry_over.id
                    record.is_carry_over = True
                    if record.state == 'validate':
                       record.number_of_carry_over = max(0.0, (carry_over.amount - record.number_of_days_temp))
                else:
                    record.carry_over_id = False
                    record.is_carry_over = False
                holidays = [
                    ('year','=',record.year),
                    ('process_type','not in',['carry_over'])
                ]
                leave_type = self.env['hr.leave.type'].search([('holiday_status_id','=',record.holiday_status_id.id)],limit=1)
                record.show_leave_credit = True if leave_type or carry_over else False

                if leave_type:
                    leave_days = leave_type.get_days(record.employee_id.id, record.holiday_status_id, holidays)[record.holiday_status_id.id]
                    record.curr_date_leave_count =  max(0.0, (leave_days['remaining_leaves'] + carry_over.used_amount))
                    expired = False if not leave_type.is_expiration else  datetime.strptime(leave_type.expiration_date, "%Y-%m-%d")
                    record.curr_expiration_date = expired


    @api.onchange('date_from','date_to')
    def get_leave_year(self):
        if self.date_from and not self.process_type:
            year_today = datetime.strptime(self.date_from,DEFAULT_SERVER_DATETIME_FORMAT).year
            self.year = year_today

    @api.constrains('date_from','date_to','curr_expiration_date')
    def _check_expired_leave(self):
        for record in self:
            leave = self.env['hr.leave.type'].search([('holiday_status_id','=',record.holiday_status_id.id)],limit=1)
            if leave and leave.is_expiration:
                if record.number_of_carry_over > 0.0 and record.carry_over_id and record.curr_expiration_date <  record.carry_over_expiration:
                    if record.date_from > record.carry_over_expiration or record.date_to > record.carry_over_expiration:
                        raise ValidationError("Your Previous Year Leave Balance is already expired on this date")
                else:
                    if record.date_from > record.curr_expiration_date or record.date_to > record.curr_expiration_date:
                        raise ValidationError("Your Current Year Leave Balance is already expired on this date")
#             if record.curr_date_leave_count == 0.0 and not record.carry_over_id:
#                 raise ValidationError(_('The number of remaining leaves is not sufficient for this leave type.\n'
#                                         'Please verify also the leaves waiting for validation.'))

class HrLeaveType(models.Model):
    _inherit = 'hr.leave.type'

    recurring_leave_type = fields.Boolean("Recurring Leave type",help="this will automatically create a new leave type")
    previous_holiday_status_id = fields.Many2one('hr.holidays.status')
    is_leave_converted_carry_over = fields.Boolean()
    is_expiration =fields.Boolean()

    def cron_create_leaves(self):
        leaves = self.env['hr.leave.type'].search([('state', '=', 'activate')])
        leaves.action_make_leaves()


    def leaves_previous_year(self):
        carry_over = self.env['ir.cron'].search([('function', '=', 'btn_carry_over_leave_convert')], limit=1)
        carry_over_year = int(datetime.strptime(carry_over.nextcall, DEFAULT_SERVER_DATETIME_FORMAT).strftime("%Y"))
        prev_year = int(carry_over_year) - 1
        return prev_year

    def btn_carry_over_leave_convert(self):
        leaves = self.env['hr.leave.type'].search([('state', '=', 'activate')])
        for lv in leaves:
            lv.action_make_conversion_carry_over()
        self.env['hr.leaves.conversion'].generate_leave_conversion(self.leaves_previous_year(), leaves)

    def action_make_conversion_carry_over(self):
        self.year = self.leaves_previous_year() + 1
        leave_type = self.env['hr.holidays.status'].browse(self.holiday_status_id.id)
        leave_type.expiration_date  =  self.expiration_date
        prev_year = self.leaves_previous_year()
        domain_earned = [('holiday_status_id', '=', self.holiday_status_id.id), ('year', '=', int(prev_year))]
        prev_year_leaves = self.env['hr.holidays'].search(domain_earned)
        prev_year_leaves.write({'expired_leave': True})
        prev_year_leaves.filtered(lambda l: l.type != 'remove').write({'active': False})
        res = super(HrLeaveType,self).action_make_conversion_carry_over()
        return res

    def create_leaves(self, record, category, employee, amount, process_type):
        holidays = self.env['hr.holidays']

        values = {
            'name': record.holiday_status_id and record.holiday_status_id.name,
            'type': 'add',
            'holiday_type': 'category',
            'holiday_status_id': record.holiday_status_id.id,
            'number_of_days_temp': 0,
            'category_id': category.id,
            'employee_id': False,
        }
        create_leaves = self.env['ir.cron'].search([('function','=','cron_create_leaves')],limit=1)
        cron_year = int(datetime.strptime( create_leaves.nextcall,DEFAULT_SERVER_DATETIME_FORMAT).strftime("%Y"))

        carry_over = self.env['ir.cron'].search([('function', '=', 'btn_carry_over_leave_convert')], limit=1)
        carry_over_year = int(datetime.strptime(carry_over.nextcall,DEFAULT_SERVER_DATETIME_FORMAT).strftime("%Y"))

        if process_type == 'earning':
             record.year = int(cron_year)
             record.holiday_status_id.expiration_date = record.expiration_date

             values['year'] =  int(cron_year)
        else:
            values['year'] = int(carry_over_year) -1

        if process_type in ('converted', 'forfeited', 'less_carry', 'expired'):
            values['type'] = 'remove'
        else:
            values['type'] = 'add'

        values['process_type'] = process_type
        values['expiration_date'] = record.expiration_date
        if process_type == 'carry_over':
            leave_type = self.env['hr.holidays.status'].browse(record.holiday_status_id.id)
            carry_over = self.env['leave.carry.over']
            values['year'] = int(carry_over_year)
            prev_year = date.today() - relativedelta(year=1)
            domain = [('employee_id','=',employee.id),('holiday_status_id','=',leave_type.id),('year','=', int(carry_over_year))]
            values['expiration_date'] = record.prev_expiration_date
            carry_over_exist = carry_over.search(domain, limit=1)
            if not carry_over_exist:
                data = {'employee_id': employee.id, 'holiday_status_id':leave_type.id,'prev_holiday_status_id':record.holiday_status_id.id, 'amount': amount,'original_amount':amount,
                        'carry_over_expiration': record.prev_expiration_date,'year': int(carry_over_year)}
                carry_over.create(data)

            else:
                carry_over_exist.update({'carry_over_expiration':record.prev_expiration_date,'original_amount':amount,'prev_holiday_status_id':record.holiday_status_id.id})

        values['holiday_type'] = 'employee'
        values['category_id'] = False
        values['employee_id'] = employee.id

        values['date_processed'] = fields.Datetime.now()
        values['number_of_days_temp'] = amount

        leaves = holidays.create(values)
        leaves.action_validate()

        return True

    @api.onchange('month')
    def get_days_in_month(self):
        if self.month:
            num_days = calendar.monthrange(date.today().year, int(self.month))[1]
            days_ids = []
            res_domain = {}
            days = ["{:02d}".format(day) for day in range(1, num_days + 1)]
            for days_of_the_week in self.env['hr.work_sched_time'].search([('month_date', 'in', days)]):
                days_ids.append(days_of_the_week.id)
            res_domain['domain'] = {'days': [('id', 'in', days_ids)]}
            return res_domain

    @api.onchange('prev_month')
    def prev_get_days_in_month(self):
        if self.prev_month:
            num_days = calendar.monthrange(date.today().year, int(self.prev_month))[1]
            days_ids = []
            res_domain = {}
            days = [ "{:02d}".format(day) for day in range(1, num_days + 1)]
            for days_of_the_week in self.env['hr.work_sched_time'].search([('month_date', 'in', days)]):
                days_ids.append(days_of_the_week.id)

            res_domain['domain'] = {'prev_days': [('id', 'in', days_ids)]}
            return res_domain

    @api.multi
    @api.depends('month','days','holiday_status_id','prev_month','prev_days','year')
    def compute_expiration(self):
        for record in self:
            if not record.year:
                years = date.today().year
            else:
                years = record.year
            if record.month and record.days:
                expiration = "%s-%s-%s"%(years,record.month,record.days.month_date)
                record.expiration_date = expiration
            if record.prev_month and record.prev_days:
                prev_expiration = "%s-%s-%s"%(years,record.prev_month,record.prev_days.month_date)
                record.prev_expiration_date = prev_expiration

    @api.onchange('is_expiration')
    def onchange_date(self):
       if not  self.is_expiration:
           self.month = False
           self.days = False
           self.prev_month = False
           self.prev_days = False


    month = fields.Selection(month_list,string="Month")
    days = fields.Many2one('hr.work_sched_time', 'days')
    expiration_date = fields.Date('Current Year Expiration',compute='compute_expiration')
    year = fields.Integer("Year")

    prev_month = fields.Selection(month_list,string="Month")
    prev_days = fields.Many2one('hr.work_sched_time', 'days')
    prev_expiration_date = fields.Date('Previous Year  Expiration',compute='compute_expiration')



class HrLeaveConversion(models.Model):
    _name= 'hr.leaves.conversion'
    _rec_name = 'employee_id'
    _order = 'year DESC'

    employee_id = fields.Many2one('hr.employee',string="Employee")
    department_id = fields.Many2one('hr.department',string="Department",related="employee_id.department_id")
    company_id = fields.Many2one('res.company',string="Company",related="employee_id.company_id")
    job_id = fields.Many2one('hr.job',string="Position",related="employee_id.job_id")
    leave_credit = fields.Float("Total Leaves to convert",compute='get_total_leave_credit')
    year = fields.Char()
    conversion_line_ids = fields.One2many('hr.leaves.conversion.line','conversion_id',ondelete="cascade")
    daily_rate = fields.Float("Daily Rate",compute='get_total_leave_credit')
    amount = fields.Float("Total Amount",compute='get_total_leave_credit')
    date_start = fields.Date("Date Start")
    date_end = fields.Date('Date End')

    @api.depends('conversion_line_ids','employee_id')
    def get_total_leave_credit(self):
        for record in self:
            record.leave_credit = sum(record.conversion_line_ids.mapped('leave_to_convert'))
            contract = self.env['hr.contract'].search([('employee_id','=',record.employee_id.id)],limit=1,order='id DESC')
            if contract.average_working_days > 0:   # was producing division by zero error
                record.daily_rate = contract.wage / contract.average_working_days
                record.amount = record.daily_rate * record.leave_credit

    def get_holidays(self,record,prev_year,args=[]):
        domain = [
            ('employee_id', '=', record.employee_id.id),
            ('state', 'in', ['confirm', 'validate1', 'validate']),
            ('holiday_status_id', '=', record.holiday_status_id.id),
            ('expired_leave', '=', True),
            ('year', '=', prev_year),

        ]
        if args:
            domain += args
        holidays = self.env['hr.holidays'].search(domain)
        return holidays

    @api.model
    def generate_leave_conversion(self,prev_year, leaves=False):
        remaining_leaves = 0.0
        leaves_convert = 0.0
        leaves = leaves or self.env['hr.leave.type'].search([('state', '=', 'activate')])
        leaves_obj = self.env['hr.holidays'].search([('year','=',prev_year),
                                                     ('holiday_status_id', 'in', leaves.mapped('holiday_status_id').ids),
                                                     ('process_type', 'in',['earning', 'carry_over']),
                                                     ('type', '=', 'add'),
                                                     ('active','=',False),
                                                     ('state', 'in', ['confirm', 'validate1', 'validate'])])
        result = {}
        for leaves in leaves_obj:
            if leaves.employee_id.id in result:
                result[leaves.employee_id.id] |= leaves
            else:
                result[leaves.employee_id.id] = leaves
        for keys,values in result.items():
            leaves_converted_line = []
            for record in values.mapped('holiday_status_id'):
                leave_automation = self.env['hr.leave.type'].search([('holiday_status_id', '=', record.id), ('state', '=', 'activate')], limit=1)
                leaves_to_convert = leave_automation.leave_conversion
                auto_carry_over = leave_automation.carry_over
                employee = self.env['hr.employee'].browse(keys)

                leave_days = leave_automation.get_leave_days(auto_carry_over, leaves_to_convert, prev_year, employee, record)
                if leave_days['last_year_earned_leaves']:
                    leaves_converted_line.append((0, 0, 
                                {'leave_type_id': record.id,'leave_credit':leave_days['last_year_earned_leaves'],
                                 'used_leaves':leave_days['leaves_taken'],'leave_to_convert':leave_days['leaves_convert'],
                                 'leave_carry_over':leave_days['to_carry_over'],
                                 'last_carry_over':leave_days['last_year_carry_over']}))

            leave_conversion_obj =  self.env['hr.leaves.conversion'].search([('employee_id','=',keys),('year','=',prev_year)],limit=1)
            if leave_conversion_obj:
                leave_conversion_obj.conversion_line_ids = [(6,0,[])]
                converted_obj = leave_conversion_obj.update({'conversion_line_ids': leaves_converted_line})
                if converted_obj and converted_obj.leave_credit <= 0.0:
                    converted_obj.sudo().unlink()
            elif leave_days['credit_leaves'] > 0:
                convert_dict = {'employee_id': keys,
                           'year': prev_year,
                           'conversion_line_ids': leaves_converted_line,
                          }
                converted_obj = self.env['hr.leaves.conversion'].create(convert_dict)

class HrLeaveConversionline(models.Model):
    _name = 'hr.leaves.conversion.line'

    conversion_id = fields.Many2one('hr.leaves.conversion')
    leave_type_id = fields.Many2one('hr.holidays.status',string="Leave Type")
    leave_credit = fields.Float("Leave Credit")
    last_carry_over = fields.Float("Last Year Carry Over")
    leave_to_convert = fields.Float("Leaves converted")
    leave_carry_over = fields.Float("Leaves Carry Over")
    used_leaves = fields.Float("Used Leaves")

#     @api.depends('leave_type_id')
#     def get_carry_over(self):
#         for record in self:
#             year = int(record.conversion_id.year) + 1
#             leaves_carry_over = self.env['leave.carry.over'].search([('holiday_status_id','=',record.leave_type_id.id),('year','=',year),('employee_id','=',record.conversion_id.employee_id.id)],limit=1)
#             record.leave_carry_over = leaves_carry_over.original_amount if leaves_carry_over else 0.0


class WorkScheduleTime(models.Model):
    _name = 'hr.work_sched_time'
    _rec_name = 'month_date'

    month_date = fields.Char()
    is_month_date = fields.Boolean()


class DeleteAllLeaves(models.TransientModel):
    _name ='delete.leaves'

    year = fields.Integer()

    def delete_leaves(self):
        self._cr.execute("DELETE FROM hr_holidays WHERE year=%s;"%(self.year))
        for record in self.env['leave.carry.over'].search([('year','=',(self.year + 1))]):
            for carry_line in record.carry_over_line:
                carry_line.sudo().unlink()
            record.sudo().unlink()
        for convert in self.env['hr.leaves.conversion'].search([('year','=',str(self.year))]):
            for line in convert.conversion_line_ids:
                line.sudo().unlink()
            convert.sudo().unlink()

