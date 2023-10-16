#-*-coding:utf-8-*-
from odoo import models, fields, api
from odoo.tools import float_compare
from odoo.exceptions import ValidationError,UserError
from odoo.tools.translate import _
from datetime import datetime,timedelta
from dateutil.relativedelta import relativedelta

MONTHS = 12
START_MONTH = 1 
END_MONTH = 12
START_DAY = 1
END_DAY = 1
LEAVE_PER_DAY = 8

def get_monthly(values):
    return values / MONTHS 
    
def prorated_leaves(values, current_month):
    return get_monthly(values) * (13 - current_month)

class HRLeavesType(models.Model):
    _name = 'hr.leave.type'
    _description = 'HR Leaves Type'
    _rec_name = 'holiday_status_id'
    
    @api.multi
    def prepare_leave_values(self, record, category):
        """Return holiday values."""
        values = {
            'name': record.holiday_status_id and record.holiday_status_id.name,
            'type': 'add',
            'holiday_type': 'category',
            'holiday_status_id': record.holiday_status_id.id,
            'number_of_days_temp': 0,
            'category_id': category.id,
            'employee_id': False
            }
        return values
    
    def create_leaves(self, record, category, employee, amount, process_type):
        """Generate leaves for specific employee depending each process type."""
        holidays = self.env['hr.holidays']
        values = self.prepare_leave_values(record, category)
        
        if process_type in ('converted', 'forfeited', 'less_carry', 'expired'):
            values['type'] = 'remove'        
        else:
            values['type'] = 'add'
            
        values['holiday_type'] = 'employee'
        values['category_id'] = False
        values['employee_id'] = employee.id
        values['process_type'] = process_type
        values['date_processed'] = fields.Datetime.now()
        values['number_of_days_temp'] = amount
        
        leaves = holidays.create(values)
        leaves.action_validate()
        
        return True
    
    def cron_make_conversion_carry_over(self, cron_id):
        if cron_id:
            leaves = self.env['hr.leave.type'].search([('cron_id', '=', cron_id), ('state','=', 'activate')])
            leaves.action_make_conversion_carry_over()
    
    @api.multi
    def get_days(self, employee_id, holiday_status_id, args=[]):
        """Returns the leaves computation."""
        # need to use `dict` constructor to create a dict per id
        result = dict((id, dict(max_leaves=0, leaves_taken=0, remaining_leaves=0, virtual_remaining_leaves=0)) for id in [holiday_status_id.id])
        domain = [
            ('employee_id', '=', employee_id),
            ('state', 'in', ['confirm', 'validate1', 'validate']),
            ('holiday_status_id', '=', holiday_status_id.id),
        ]
        if args:
            domain += args
            
        holidays = self.env['hr.holidays'].search(domain)
        for holiday in holidays:

            status_dict = result[holiday.holiday_status_id.id]
            if holiday.type == 'add':
                if holiday.state == 'validate':
                    # note: add only validated allocation even for the virtual
                    # count; otherwise pending then refused allocation allow
                    # the employee to create more leaves than possible
                    status_dict['virtual_remaining_leaves'] += holiday.number_of_days_temp
                    status_dict['max_leaves'] += holiday.number_of_days_temp
                    status_dict['remaining_leaves'] += holiday.number_of_days_temp
            elif holiday.type == 'remove':  # number of days is negative
                status_dict['virtual_remaining_leaves'] -= holiday.number_of_days_temp
                if holiday.state == 'validate':
                    status_dict['leaves_taken'] += holiday.number_of_days_temp
                    status_dict['remaining_leaves'] -= holiday.number_of_days_temp
        return result
    
    def cron_expire_leaves(self, cron_id):
        if cron_id:
            leaves = self.env['hr.leave.type'].search([('cron_id', '=', cron_id), ('state','=', 'activate')])
            leaves.action_expire_leaves()
    
    def action_expire_leaves(self):
        """Create expire leaves.
        Compares carried over leaves and leaves taken to
        get the leave days to be expired."""
        for record in self.filtered(lambda record:record.state == 'activate' and record.with_expiration):
            
            for category in record.categ_ids:
                
                employee_ids = category.employee_ids
                if record.gender:
                    employee_ids = employee_ids.filtered(lambda r:r.gender == record.gender)
                
                for employee in employee_ids:
                    
                    validity_start_date = fields.Datetime.to_string(datetime.now().replace(month=1, day=1, hour=0, minute=0, second=0))
                    validity_end_date = fields.Datetime.to_string(fields.Datetime.from_string(record.expiration_date).replace(year=datetime.now().year))
                    
                    args = [
                        ('process_type', '=', 'carry_over'),
                        ('date_processed', '>=', validity_start_date),
                        ('date_processed', '<=', validity_end_date),
                        ('type', '=', 'add')
                    ]
    
                    carried_over_leave_days = self.get_days(employee.id, record.holiday_status_id, args)[record.holiday_status_id.id]
                    remaining_leaves = carried_over_leave_days['remaining_leaves']
                    
                    #skip if no remaining leaves
                    if float_compare(carried_over_leave_days['remaining_leaves'], 0, precision_digits=2) == -1 or \
                    float_compare(carried_over_leave_days['virtual_remaining_leaves'], 0, precision_digits=2) == -1:
                        continue
                    
                    args = [
                        ('date_approved', '>=', validity_start_date),
                        ('date_approved', '<=', validity_end_date),
                        ('type', '=', 'remove'),
                        ('process_type', '=', False)
                    ]
                    
                    leave_days = self.get_days(employee.id, record.holiday_status_id, args)[record.holiday_status_id.id]
                    leaves_taken = leave_days['leaves_taken']
                    
                    if leaves_taken < remaining_leaves:
                        leaves_to_expire = remaining_leaves - leaves_taken
                        self.create_leaves(record, category, employee, leaves_to_expire, 'expired')

    def get_leave_days(self, auto_carry_over, leaves_to_convert, year, employee, holiday_status_id):
        #Last year earned leaves
        args = [
                ('year', '=', year),
                ('process_type', '=', 'earning'),
                ('active', '=', False)
            ]
        leave_days = self.get_days(employee.id, holiday_status_id, args)[holiday_status_id.id]
        last_year_earned_leaves = leave_days['remaining_leaves']

        #Last year carry over
        args = [
                ('year', '=', year),
                ('process_type', '=', 'carry_over'),
                ('active', '=', False)
            ]
        carry_leave_days = self.get_days(employee.id, holiday_status_id, args)[holiday_status_id.id]
        last_year_carry_over = carry_leave_days['remaining_leaves']

        #Used Leaves
        args = [
                ('year', '=', year),
                ('process_type', '=', False)
            ]
        used_leave_days = self.get_days(employee.id, holiday_status_id, args)[holiday_status_id.id]
        leaves_taken = used_leave_days['leaves_taken']

        #Credit Leaves
        credit_leaves = last_year_earned_leaves + last_year_carry_over

        if leaves_taken < last_year_carry_over:
            remaining_leaves = last_year_earned_leaves
        else:
            remaining_leaves = credit_leaves - leaves_taken

        less_carry = 0
        if self.priority2 < self.priority:
            if remaining_leaves >= leaves_to_convert:
                remaining_leaves -= leaves_to_convert
                leaves_convert = leaves_to_convert
                to_carry_over = remaining_leaves if remaining_leaves <= auto_carry_over else auto_carry_over
                less_carry = remaining_leaves - to_carry_over > 0 and remaining_leaves - to_carry_over or 0
            else:
                leaves_convert = remaining_leaves
                to_carry_over = 0
        else:
            if remaining_leaves >= auto_carry_over:
                to_carry_over = auto_carry_over
                remaining_leaves -= auto_carry_over
                leaves_convert = remaining_leaves if remaining_leaves <= leaves_to_convert else leaves_to_convert
                less_carry = (remaining_leaves - leaves_convert) > 0 and (remaining_leaves - leaves_convert) or 0
            else:
                to_carry_over = remaining_leaves
                leaves_convert = 0

        return {'last_year_earned_leaves': last_year_earned_leaves,
            'last_year_carry_over': last_year_carry_over,
            'leaves_taken': leaves_taken,
            'credit_leaves': credit_leaves,
            'leave_days': leave_days,
            'leaves_convert': leaves_convert,
            'to_carry_over': to_carry_over,
            'less_carry': less_carry}

    def action_make_conversion_carry_over(self):
        """Create leave conversion and carry over values."""
        employee_ids = self.env['hr.employee']
#         remaining_leaves = 0.0
        for record in self.filtered(lambda record:record.state == 'activate'):
            for category in record.categ_ids:
                employee_ids += category.employee_ids
                if record.gender:
                    employee_ids = employee_ids.filtered(lambda r:r.gender == record.gender)

        for employee in employee_ids:
            auto_carry_over = record.carry_over
            leaves_to_convert = record.leave_conversion
            leave_days = self.get_leave_days(auto_carry_over, leaves_to_convert, record.year-1, employee, record.holiday_status_id)

            #skip if no remaining leaves
            if float_compare(leave_days['leave_days']['remaining_leaves'], 0, precision_digits=2) == -1 or \
            float_compare(leave_days['leave_days']['virtual_remaining_leaves'], 0, precision_digits=2) == -1:
                continue
            if leave_days['to_carry_over'] > 0:
                self.create_leaves(record, category, employee, leave_days['to_carry_over'], 'carry_over')

            if leave_days['leaves_convert'] > 0:
                self.create_leaves(record, category, employee, leave_days['leaves_convert'], 'converted')

            if leave_days['less_carry'] > 0:
                self.with_context(from_scheduler=True).create_leaves(record, category, employee, leave_days['less_carry'], 'less_carry')

            #forfeit remaining leaves 
#                 if remaining_leaves > 0:
#                     self.create_leaves(record, category, employee, remaining_leaves, 'forfeited')             

        return True

    def cron_make_leaves(self, cron_id):
        if cron_id:
            leaves = self.env['hr.leave.type'].search([('cron_id', '=', cron_id), ('state','=', 'activate')])
            leaves.action_make_leaves()
        
    @api.multi
    def action_make_leaves(self):
        """Create leave allocation."""
        
        for record in self.filtered(lambda record:record.state == 'activate'):
            amount = record.amount

            
            for category in record.categ_ids:

                
                date_now =  datetime.utcnow()
                month_today = date_now.strftime('%Y-%m')
                
                start_date = datetime.strptime(month_today, '%Y-%m')
                end_date = start_date + relativedelta(days=30)
                
                date_f = start_date.strftime('%Y-%m-%d')
                date_t = end_date.strftime('%Y-%m-%d')
                
                old_employees = category.employee_ids
                new_employees = self.get_employees_ids(category, date_f, date_t, record.basis_entitlement)

                if record.gender:
                    old_employees = old_employees.filtered(lambda e:e.gender == record.gender)
                    new_employees = new_employees.filtered(lambda n:n.gender == record.gender)
                    
                diff = old_employees - new_employees
                
                if record.basis_earning == 'monthly':
                    for employees in old_employees:
                        
                        amount = get_monthly(record.amount)
                        self.create_leaves(record, category, employees, amount, 'earning')
                        
                if record.basis_earning == 'year_start':
                    if record.force_create:
                        current_month = 1
                    else:
                        current_month = date_now.month

                    for new_employee in new_employees:
                        amount = prorated_leaves(record.amount, current_month)
                        self.create_leaves(record, category, new_employee, amount, 'earning')

                    if current_month == START_MONTH:
                        for old_employee in diff:
                            amount = record.amount
                            self.create_leaves(record, category, old_employee, amount, 'earning')
               
                if record.basis_earning == 'year_end':
                     
                    date_f = date_now.replace(month=START_MONTH, day=START_DAY).strftime('%Y-%m-%d')
                    date_t = date_now.replace(month=END_MONTH, day=END_DAY).strftime('%Y-%m-%d')
                    
                    new_employees = self.get_employees_ids(category, date_f, date_t, record.basis_entitlement)
                    if record.gender:
                        new_employees = new_employees.filered(lambda n:n.gender == record.gender)
                        
                    diff = old_employees - new_employees


                    current_month = date_now.month
                    if current_month == END_MONTH:
                         
                        for new_employee in new_employees:
                            if record.basis_entitlement == 'hired':
                                entitlement_month = new_employee.date_start
                            else:
                                entitlement_month = new_employee.date_permanent
                            
                            entitlement_month = fields.Date.from_string(entitlement_month).month
                            
                            amount = prorated_leaves(record.amount, entitlement_month)
                            self.create_leaves(record, category, new_employee, amount, 'earning')
                            
                        for old_employee in diff:
                            amount = record.amount
                            self.create_leaves(record, category, old_employee, amount, 'earning')
                record.force_create = False
                        
    def get_employees_ids(self, categories, start_date, end_date, basis):
        """Return employees between dates base on entitlement."""
        
        date_f =  fields.Datetime.from_string(start_date)
        date_t = fields.Datetime.from_string(end_date)
        
        date_from = fields.Datetime.context_timestamp(self, date_f)
        date_to = fields.Datetime.context_timestamp(self, date_t)
        
        date_start =  fields.Date.to_string(date_from)
        date_end = fields.Date.to_string(date_to)
        
        employees = self.env['hr.employee']
    
        for record in categories:
            employee_ids = record.employee_ids
            
            by_date_hired = lambda r:(r.contract_id.date_start <= date_end) \
                             and not r.contract_id.resigned and r.contract_id.state in ('open', 'pending')
            by_date_permanent = lambda r:(r.contract_id.date_permanent <= date_end)\
                                  and not r.contract_id.resigned and r.contract_id.state in ('open', 'pending')
            
            entitlement_mode = by_date_hired if basis == 'hired' \
            else by_date_permanent
    
            employees |= employee_ids.filtered(entitlement_mode)
            
        return employees
        
    @api.multi
    def action_activate(self):
        for record in self:
            if record.cron_id:
                record.cron_id.write({'args': '(%s,)'%record.cron_id.id})
        return self.write({'state': 'activate'})
    
    @api.multi
    def action_deactivate(self):
        return self.write({'state': 'deactivate'})
    
    def btn_activate(self):
        self.action_activate()
    
    def btn_deactivate(self):
        self.action_deactivate()   
    
    def onchange_reset(self):
        self.carry_over = 0
        self.leave_conversion = 0
        self.priority = 0
        self.priority2 = 0
        
    @api.onchange('basis_earning', 'basis_entitlement')
    def onchange_basis(self):
        self.onchange_reset()
         
    @api.constrains('priority', 'priority2', 'leave_conversion', 'carry_over')
    def check_priority(self):
        for record in self:
            if (record.priority or record.priority2 or record.carry_over or record.leave_conversion)\
             and record.priority == record.priority2:
                raise ValidationError(_('Record priority must not be equal!'))
        
    def btn_make_conversion_carry_over(self):
        self.action_make_conversion_carry_over()    
    
    def btn_make_leaves(self):
        self.action_make_leaves()
    
    def btn_expire_leaves(self):
        self.action_expire_leaves()
        
    holiday_status_id = fields.Many2one('hr.holidays.status', 'Leave Type', required=True)
    amount = fields.Float('Amount', required=True, default=0)
    basis_entitlement = fields.Selection([('hired', 'Date Hired'), 
                                          ('permanency', 'Date Permanency')],
                                          'Basis of Entitlement')
    
    basis_earning = fields.Selection([('monthly', 'Monthly'), 
                                      ('year_start', 'Annually Year Start'),
                                      ('year_end', 'Annually Year End')],
                                      'Basis of Earning')

    carry_over = fields.Float('Max to Carry Over', default=0)
    priority = fields.Integer('Priority')
    leave_conversion = fields.Float('Max Leave Conversion', default=0)
    priority2 = fields.Integer('Priority')
    categ_ids = fields.Many2many('hr.employee.category', 
                                 'hr_employee_leave_rel', 
                                 'categ_id', 'leave_type_id', 'Employee Tag')
    gender = fields.Selection([('male', 'Male'), 
                               ('female','Female'), 
                               ('other', 'Other')], 
                              'Gender', help='Applies to specific gender')
    
    with_expiration = fields.Boolean('With Expiration', help="Carried Over leaves has expiration")
    expiration_date = fields.Date('Date of Expiration')
    
    cron_id = fields.Many2one('ir.cron', 'Schedule')
    state = fields.Selection([('activate', 'Activated'), 
                              ('deactivate', 'Deactivated')], 'State', default='deactivate')

    force_create = fields.Boolean()
    _sql_constraints = [
            ('leave_conversion_check', "CHECK( leave_conversion >= 0 )", "The max leave conversion must be greater than 0!"),
            ('carry_over_check', "CHECK( carry_over >= 0 )", "The max to carry over must be greater than 0!")]

HOURS_PER_DAY = 8
import math

def float_time_convert(float_value):
    """Splits float value into hour and minute."""
    minute, hour = math.modf(float_value)
    minute = minute * 60
    hour = int(round(hour, 2))
    minute = int(round(minute, 2))

    return hour, minute

class CalendarLeaves(models.Model):

    _inherit = "resource.calendar.leaves"
    _description = "Leave Detail"

    employee_id = fields.Many2one('hr.employee')


class HRLeave(models.Model):
    _inherit = 'hr.holidays'

    @api.model
    def leaves_filter_act(self):
        hr_emp = self.env['hr.employee'].search([('user_id', '=', self.env.user.id)])

        if self.env.user.has_group('hris.group_approver') or self.env.user.has_group('hris.group_firstlevel'):
            return {
                'name': _("Leaves"),
                'type': 'ir.actions.act_window',
                'res_model': 'hr.holidays',
                'view_mode': 'tree,form',
                'view_type': 'form',
                'domain': [('employee_id.user_id','=',self.env.uid),('type','=','add')],
                'views': [[False, 'tree'], [False, 'form'], ],
                'context': {
                            'default_type':'add',
                            'search_default_my_leaves': 1,
                            'needaction_menu_ref':['hr_holidays.menu_open_company_allocation',] } ,

                'target': 'current',
            }
        else:
            return {
                'name': _("Leaves"),
                'type': 'ir.actions.act_window',
                'res_model': 'hr.holidays',
                'view_mode': 'tree,form',
                'view_type': 'form',
                'domain':[],
                'views': [[False, 'tree'], [False, 'form'], ],
                'context': {
                            'default_type':'add',
                            'search_default_my_leaves': 1,
                            'needaction_menu_ref':['hr_holidays.menu_open_company_allocation',] } ,
                'target': 'current',
            }


    @api.constrains('employee_id','holiday_status_id','date_from','date_to','number_of_days_temp','department_id','name','company_id')
    def validate_edit(self):
        context = self._context
        current_uid = context.get('uid')
        user = self.env['res.users'].browse(current_uid)
        approver = self.env['res.users'].has_group('hris.group_approver')
        current_user = self.env.user.groups_id.mapped('id')
        for record in self:
            if record.employee_id.user_id.id != user.id and user.id != 1 and approver in current_user:
                raise ValidationError("Unable to edit/create others record")


    @api.multi
    @api.depends('number_of_days_temp', 'type')
    def _compute_number_of_days(self):
        for holiday in self:
            if holiday.type == 'remove':
                holiday.number_of_days = -holiday.number_of_days_temp
                holiday.actual_number_of_days = holiday.number_of_days_temp

            else:
                holiday.number_of_days = holiday.number_of_days_temp
    
    def get_worked_hours(self, work_schedule, date_today, date_in, date_out):
        """Return worked hours equivalent of the filed leaves."""
        #schedule_type = work_schedule.work_time_id and work_schedule.work_time_id.schedule_type
            
        required_in_hour,required_in_minute = float_time_convert(work_schedule.latest_check_in) 
        required_in = fields.Datetime.context_timestamp(self, date_today).replace(hour=required_in_hour, minute=required_in_minute) 
        
        required_out_hour,required_out_minute = float_time_convert(work_schedule.time_to_render)
        required_out = required_in + timedelta(hours=required_out_hour, minutes=required_out_minute)
        
        worked_hours = 0
           
        if work_schedule.break_period:

            break_period_hour, break_period_minute = float_time_convert(work_schedule.break_period)

            lunch_break = required_in + timedelta(hours=4, minutes=0, seconds=0)
            lunch_break_period = lunch_break + timedelta(hours=break_period_hour, minutes=break_period_minute, seconds=0)

            if date_in < lunch_break:
                worked_hours += (min([date_out, lunch_break]) - max([date_in, required_in])).total_seconds() / 3600.0

            if lunch_break_period < required_out and date_out > lunch_break_period:
                worked_hours += (min([date_out, required_out]) - max([lunch_break_period, required_in])).total_seconds() / 3600.0

            if date_in > lunch_break_period and date_in < required_out:
                worked_hours = (min([date_out, required_out]) - max([date_in, lunch_break_period])).total_seconds() / 3600.0
                    
            if lunch_break <= date_in <= lunch_break_period: 
                worked_hours = (min([date_out, required_out]) - max([date_in, lunch_break_period])).total_seconds() / 3600.0
            
            if lunch_break <= date_in <= date_out <= lunch_break_period:
                worked_hours = 0
        else:
            worked_hours = (min([date_out, required_out]) - max([date_in, required_in])).total_seconds() / 3600.0
        
        return worked_hours
    
    def compute_employee_leave_hours(self):
        """Computes employee leave hours."""
        
        day_from = fields.Datetime.from_string(self.date_from)
        day_to = fields.Datetime.from_string(self.date_to)
        nb_of_days = (day_to - day_from).days + 1
        
        date_in = fields.Datetime.context_timestamp(self, day_from)
        date_out = fields.Datetime.context_timestamp(self, day_to)
             
        # Gather all intervals and holidays
  
        total_worked_hours = 0
        for day in range(0, nb_of_days):

            date_today = day_from + timedelta(days=day)
            
            today = fields.Datetime.context_timestamp(self, date_today)
            date_utc_today = fields.Datetime.to_string(date_today)
            employee = self.employee_id
            
            day_name = today.strftime('%A').lower()
            # insert here filtering based on date hired
            
            work_schedule = False
            work_schedule_by_employee = self.env['hr.attendance'].get_employee_worked_schedule(employee, False, date_utc_today, day_name)
            if work_schedule_by_employee:
                work_schedule = work_schedule_by_employee
            
            # If no work schedule by employee found
            if not work_schedule_by_employee:
                work_schedule = self.env['hr.attendance'].get_employee_worked_schedule(employee, 
                                                                                employee.department_id, 
                                                                               date_utc_today, day_name)
            if not work_schedule:
                continue
            
            total_worked_hours += self.get_worked_hours(work_schedule, date_today, date_in, date_out)
            
        return total_worked_hours

    def _get_number_of_days(self, date_from, date_to, employee_id):
        """ Returns a float equals to the timedelta between two dates given as string."""
        from_dt = fields.Datetime.from_string(date_from)
        to_dt = fields.Datetime.from_string(date_to)

        if employee_id:
            employee = self.env['hr.employee'].browse(employee_id)
            resource = employee.resource_id.sudo()
            if resource and resource.calendar_id:
                hours = resource.calendar_id.get_working_hours(from_dt, to_dt, resource_id=resource.id, compute_leaves=True)
                uom_hour = resource.calendar_id.uom_id
                uom_day = self.env.ref('product.product_uom_day')
                if uom_hour and uom_day:
                    return uom_hour._compute_quantity(hours, uom_day)

        time_delta = to_dt - from_dt
        hours = time_delta.days + float(time_delta.seconds) / 28800
        return round(hours * 2) / 2

    @api.constrains('date_from')
    def _check_lockout_period(self):
        for holiday in self:
            
            if holiday.holiday_type != 'employee' or holiday.type != 'remove' or not holiday.employee_id or not holiday.holiday_status_id.lockout:
                continue
            
            today = fields.Date.today()
            date_from = fields.Date.to_string(fields.Datetime.context_timestamp(self, fields.Datetime.from_string(holiday.date_from)))
            domain = [
                ('start_date', '<=', today),
                ('date_release', '>=', date_from)
            ]

            cutoffs = self.env['hr.payroll.period_line'].search_count(domain)
            if cutoffs > holiday.holiday_status_id.lockout_period:
                raise ValidationError(_('Unable to file or process leaves.The lockout period has been reached!'))


    @api.constrains('date_from')
    def _check_notice_period(self):
        for holiday in self:
            
            if holiday.holiday_type != 'employee' or holiday.type != 'remove' or not holiday.employee_id or not holiday.holiday_status_id.notice:
                continue
            
            today = fields.Date.today()
            date_from = fields.Date.to_string(fields.Datetime.context_timestamp(self, fields.Datetime.from_string(holiday.date_from)))
            
            date_t = fields.Date.from_string(today)
            date_f = fields.Date.from_string(date_from)
            if today <= date_from:
                days = (date_f - date_t).days
                
                if days < holiday.holiday_status_id.notice_period:
                    raise ValidationError(_('Filing of %s must have %d days notice')%(holiday.holiday_status_id.name, holiday.holiday_status_id.notice_period))

    @api.constrains('state', 'number_of_days_temp', 'date_approved')
    def _check_holidays(self):
        if self._context.get('from_scheduler'):
            return
        for holiday in self:
            if holiday.holiday_type != 'employee' or holiday.type != 'remove' or not holiday.employee_id or holiday.holiday_status_id.limit:
                continue
            
            curr_date = datetime.utcnow()
            year = int(curr_date.strftime('%Y'))
             
            if holiday.holiday_status_id.expiration_date:
                expiration_date = fields.Date.from_string(holiday.holiday_status_id.expiration_date).replace(year=year)
                current_date = fields.Date.from_string(curr_date.strftime('%Y-%m-%d'))
           
                if current_date >= expiration_date:
                    raise ValidationError(_('Leaves already expired.'))

            leave_days = holiday.holiday_status_id.get_days(holiday.employee_id.id)[holiday.holiday_status_id.id]
            if float_compare(leave_days['remaining_leaves'], 0, precision_digits=2) == -1 or \
              float_compare(leave_days['virtual_remaining_leaves'], 0, precision_digits=2) == -1:
                raise ValidationError(_('The number of remaining leaves is not sufficient for this leave type.\n'
                                            'Please verify also the leaves waiting for validation.'))

    @api.multi
    def get_employee_attendance(self):
        """Return employee attendances that matched on the approved leaves."""
        attendance = self.env['hr.attendance']
        for record in self:
            
            domain2 = [
                ('employee_id', '=', record.employee_id.id),
                ('schedule_in', '<=', record.date_to),
                ('schedule_out','>=', record.date_from)]
            
            attendance |= self.env['hr.attendance'].search(domain2)
            
        return attendance
    
    @api.multi
    def remove_from_attendance(self):
        """Remove mapped leaves from the attendances if reset to draft."""
        attendances = self.get_employee_attendance()
        if attendances:
            
            for record in self:
                vals = {}
                for att in attendances:
                    if not att.is_raw:
                        vals['is_absent'] = True
                        vals['remarks'] = 'ABS'
                    
                    vals['leave_ids'] = [(3, record.id)]
                    vals['is_leave'] = False
                
                    att.write(vals)
    
    @api.multi
    def is_adjustment(self):
        """Sets if an adjustment"""
        for record in self:
            domain = [
                ('employee_id', '=', record.employee_id.id),
                ('date_from', '<=', record.date_from),
                ('date_to', '>=', record.date_from), ('state', '=', 'done')]
            
            payslip = self.env['hr.payslip'].search(domain)
            
            domain = [
                ('employee_id', '=', record.employee_id.id),
                ('date_from', '<=', record.date_approved),
                ('date_to', '>=', record.date_approved), ('state', '=', 'done')]
            
            payslip |= self.env['hr.payslip'].search(domain)
           
            if payslip:
                
                record.write({'leave_adjustment': True})
        
    @api.multi
    def set_to_attendance(self):
        """Set employee leaves to attendances if approved."""
        attendances = self.get_employee_attendance()
        if attendances:
            for record in self:
                for att in attendances:
                    vals = {}
                    
                    if att.is_suspended:
                        raise ValidationError(_('Unable approved leave\The employee is suspended.'))
                    
                    if not att.is_suspended:
                        vals['leave_ids'] = [(4, record.id)]
                        vals['remarks'] = record.holiday_status_id.name
                        
                    if not att.is_raw:
                        vals['is_absent'] = False
                        vals['is_leave'] = True
                    
                    if 'is_leave' in vals and vals.get('is_leave'):
                        
                        #Override is_leave if less than 8 hours to remove the tag as leave attendance
                        if not (att.leave_hours > LEAVE_PER_DAY  \
                        or att.leave_wop_hours > LEAVE_PER_DAY \
                        or att.ob_hours > LEAVE_PER_DAY): 
                            
                            vals['is_leave'] = False
                            
                            #Still tag as absent if there's no actual time in and actual time out
                            if not att.is_raw:
                                vals['is_absent'] = True 
                                
                    att.write(vals)

    @api.multi
    def action_draft(self):
        res = super(HRLeave,self).action_draft()

        for holiday in self:
            if self.env.uid != 1 and self.env.uid == holiday.employee_id.user_id.id:
                raise ValidationError(_('Unable to reset to draft own leave requests!'))

        return res

                    
    @api.multi
    def action_refuse(self):
        res = super(HRLeave, self).action_refuse()
        if not (self.env.user.has_group('hris.group_approver') or self.env.user.has_group('hris.group_hr_user') or self.env.user.has_group('hris.payroll_admin')):
            raise UserError(_('Only an Approver can disapprove leaves requests.'))

        if self.env.uid != 1 and self.env.uid == self.employee_id.user_id.id:
            raise ValidationError(_('Unable to disapprove own leave requests!'))

        for record in self:
            if record.type == 'remove' and record.process_type == False: 
                record.remove_from_attendance()
        
        return res
    
    def apply_policy(self, record):
        if self.env.uid != 1 and self.env.uid == record.employee_id.user_id.id:
                raise ValidationError(_('Unable to approve own leave requests!'))

        if record.type == 'remove' and record.process_type == False:
            record._check_notice_period()
            record._check_lockout_period()
            record.set_to_attendance()
            record.is_adjustment()


    # @api.multi
    # def action_approve(self):
    #     # raise ValidationError(self.id)      
    #     """Check notice period and lockout period before approving."""
    #     res = super(HRLeave, self).action_approve()
    #     if not (self.env.user.has_group('hris.group_approver') or self.env.user.has_group('hris.group_hr_user') or self.env.user.has_group('hris.payroll_admin')):
    #         raise UserError(_('Only an Approver can approve leaves requests.'))
        
    #     for record in self:
    #         record.apply_policy(record)
    #         # record.env['hr.attendance'].create({
    #         #     'employee_id': self.employee_id.id,
    #         #     'check_in': self.date_from,
    #         #     'check_out': self.date_to,
    #         # })

    #         # record.write({'leave_ids': [(4, self.id)],
    #         #               'date_approved': fields.Datetime.now()})
    #         # record.write({'date_approved': fields.Datetime.now()})
    #     return res
      
    @api.onchange('process_type')
    def onchange_process_type(self):
        if not self.process_type:
            self.date_processed = ''
    
    @api.multi
    def action_validate(self):
        if not self.env.user.has_group('hr_holidays.group_hr_holidays_user'):
            raise UserError(_('Only an HR Officer or Manager can approve leave requests.'))
        
        if not (self.env.user.has_group('hris.group_approver') or self.env.user.has_group('hris.group_hr_user') or self.env.user.has_group('hris.payroll_admin')):
            raise UserError(_('Only an Approver can approve leave requests.'))
        
        manager = self.env['hr.employee'].search([('user_id', '=', self.env.uid)], limit=1)
        for holiday in self:
            if holiday.state not in ['confirm', 'validate1']:
                raise UserError(_('Leave request must be confirmed in order to approve it.'))
            if holiday.state == 'validate1' and not holiday.env.user.has_group('hr_holidays.group_hr_holidays_manager'):
                raise UserError(_('Only an HR Manager can apply the second approval on leave requests.'))
            
            if self.env.uid != 1 and self.env.uid == holiday.employee_id.user_id.id:
                raise ValidationError(_('Unable to approve own leave requests!'))
            
            holiday.apply_policy(holiday)
            holiday.write({'state': 'validate', 'date_approved': fields.Datetime.now()})
            
            if holiday.double_validation:
                holiday.write({'manager_id2': manager.id})
            else:
                holiday.write({'manager_id': manager.id})
            if holiday.holiday_type == 'employee' and holiday.type == 'remove' and not holiday.process_type:    
                meeting_values = {
                    'name': holiday.display_name,
                    'categ_ids': [(6, 0, [holiday.holiday_status_id.categ_id.id])] if holiday.holiday_status_id.categ_id else [],
                    'duration': holiday.number_of_days_temp * HOURS_PER_DAY,
                    'description': holiday.notes,
                    'user_id': holiday.user_id.id,
                    'start': holiday.date_from,
                    'stop': holiday.date_to,
                    'allday': False,
                    'state': 'open',            # to block that meeting date in the calendar
                    'privacy': 'confidential'
                }
                #Add the partner_id (if exist) as an attendee
                if holiday.user_id and holiday.user_id.partner_id:
                    meeting_values['partner_ids'] = [(4, holiday.user_id.partner_id.id)]

                meeting = self.env['calendar.event'].with_context(no_mail_to_attendees=True).create(meeting_values)
                holiday._create_resource_leave()
                holiday.write({'meeting_id': meeting.id})
            elif holiday.holiday_type == 'category':
                leaves = self.env['hr.holidays']
                for employee in holiday.category_id.employee_ids:
                    values = holiday._prepare_create_by_category(employee)
                    leaves += self.with_context(mail_notify_force_send=False).create(values)
                # TODO is it necessary to interleave the calls?
                # leaves.action_approve()
                if leaves and leaves[0].double_validation:
                    leaves.action_validate()
        return True
    
    def _compute_modify(self):
        for holiday in self:
            if self.env.user.has_group('hris.group_approver') or self.env.user.has_group('hris.group_hr_user'):
                holiday.can_modify = True
            else:
                holiday.can_modify = False
            
    process_type = fields.Selection([('converted', 'Converted'), 
                                     ('forfeited', 'Forfeited'),
                                     ('carry_over', 'Carry Over'),
                                     ('less_carry', 'Less Carry Over'),
                                     ('earning', 'Earned'),
                                     ('expired', 'Expired')
                                     ], 
                                     'Process Type', 
                                     help="* Converted: Converted Leaves\n"
                                         "* Forfeited: Forfeited Leaves\n"
                                         "* Carry Over: Leaves Carried Over\n"
                                         "* Less Carry Over: Deducted from Carry Over\n"
                                         "* Earning: Leaves from basis of earning\n"
                                         "* Expired: Expired Leaves")
    
    date_processed = fields.Datetime('Date Processed')
    date_approved = fields.Datetime('Approved Date')
    leave_adjustment = fields.Boolean('Leave Adjustment')
    
    actual_number_of_days = fields.Float('Actual Number of Days Leave', compute="_compute_number_of_days")
    can_modify = fields.Boolean('Modify Number of Days', compute="_compute_modify", help="If number of days can be modified")
    
class HRLeaveStatus(models.Model):
    _inherit = 'hr.holidays.status'
        
    @api.constrains('code')
    def _check_code(self):
        """Check duplicated leave type code."""
        for record in self:
            if record.code:
                count = self.search_count([('id', '!=', record.id), ('code', '=', record.code)])
                if count > 0:
                    raise ValidationError(_('Leave Type code must be unique!'))
                
    leave_remarks = fields.Selection([('wp', 'With Pay'), 
                                      ('wop', 'Without Pay')], 'Leave', help='Leave Remarks')
    
    lockout = fields.Boolean('Lockout', help="Enables locking out period of leaves.")
    lockout_period = fields.Float('Lockout Period', help="Lock out period(e.g. 2 payroll cut-offs)")
    notice = fields.Boolean('Notice', help="Enables leaves with days of notice before filing")
    notice_period = fields.Float('Notice Period', help="Notice period(e.g. 14 days = 2 weeks )")
    is_ob = fields.Boolean('Official Business')
    is_cdo = fields.Boolean('Entitled To')
    job_ids = fields.Many2many('hr.job', 'job_pos_leave_type_rel', 'holiday_id', 'job_id', 'Applicable For')
    expiration_date = fields.Date('Expiration Date')
    code = fields.Char('Code', size=8, help="Leave code use for conversion in payroll.")

    leave_type_selection = fields.Selection([
        ('VL', 'Vacation Leave'),
        ('SL', 'Sick Leave'),
        ('SIL', 'SIL'),
        ('BIRTL', 'Birthday Leave'),
        ('BEARL', 'Bereavement Leave'),
        ('CDO', 'CDO'),
        ('OB', 'Official Business')], 'Leave Type', )


class CalenderEvent(models.Model):
    _inherit = 'calendar.event'

    @api.multi
    def read(self, fields=None, load='_classic_read'):
        return super(CalenderEvent, self.filtered(lambda l: l.exists())).read(fields, load)


class HRHolidaySetting(models.Model):
    _name = "hr.holiday.setting"
    _description = "Holiday Setting"
    _rec_name = "holiday_type"

    holiday_type = fields.Selection([('regular','Regular'),('special','Special')], 'Holiday Type')
    before = fields.Boolean('Before')
    after = fields.Boolean('After')

    @api.constrains('holiday_type','before','after')
    def check_holiday_setting(self):
        holidays = self.env['hr.holiday.setting'].search([])
        for rec in self:
            if len(holidays.filtered(lambda l: l.holiday_type == rec.holiday_type)) > 1:
                raise ValidationError(_("You Cannot Create More Than One Record For Same Holiday Type!!"))
            
