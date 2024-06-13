#-*- coding:utf-8 -*-

from odoo import models, api, fields, _
from odoo.exceptions import ValidationError, UserError

class HREmployeeScheduleWorkTime(models.Model):
    _name = 'hr.employee.schedule.work_time'
    _description = 'Employee Work Time Schedule'

    @api.model
    def create(self, vals):
        result = super(HREmployeeScheduleWorkTime, self).create(vals)
        employee  = self.env['hr.employee'].search([('employee_num','=',result['employee_num'])],limit=1)
        if not employee:
            raise ValidationError("No employee found for employee_num %s"%(result['employee_num']))
        else:
            result.employee_id = employee.id
            result.state = 'approved'
        return result
    
    @api.multi
    def name_get(self):
        res = []
        for record in self:
            
            start_date = fields.Datetime.context_timestamp(self, fields.Datetime.from_string(record.start_date))
            end_date = fields.Datetime.context_timestamp(self, fields.Datetime.from_string(record.end_date))
        
            start_date = start_date.strftime('%B %d, %Y %I:%M %p')
            end_date = end_date.strftime('%B %d, %Y %I:%M %p')
            if record.name:
                name = '{} {}-{}'.format(record.name.encode('utf-8') or '', start_date, end_date)
            else:
                name = '{} {}-{}'.format(record.employee_id.name.encode('utf-8') or '', start_date, end_date)
            
            res.append((record.id, name))
        
        return res
    
    def action_draft(self):
        return self.write({'state': 'draft'})
    
    def action_approved(self):
        return self.write({'state': 'approved'})
    
    def action_disapproved(self):
        return self.write({'state': 'disapproved'})
        
    def action_cancelled(self):
        return self.write({'state': 'cancelled'})
    
    @api.multi
    def btn_make_draft(self):
        self.action_draft()
    
    @api.multi
    def btn_approved(self):
        self.action_approved()
        
    @api.multi
    def btn_cancelled(self):
        self.action_cancelled()
    
    @api.multi
    def btn_disapproved(self):
        self.action_disapproved()
    
    @api.onchange('employee_id')
    def _onchange_employee_info(self):
        for rec in self:
            if rec.employee_id:
                emp = rec.employee_id
                self.name = emp.name
                self.employee_num = emp.employee_num
                self.department_id = emp.department_id
            else:
                self.name = False
                self.employee_num = False
                self.department_id = False

    @api.onchange('schedule_type')
    def _onchange_schedule_type(self):
        if self.schedule_type == 'flexible':
            self.night_shift = False
    
    @api.constrains('schedule_type','night_shift')
    def _restrict_flexible(self):
        if self.schedule_type == 'flexible' and self.night_shift:
            raise ValidationError(_("You cannot set 'Graveyard Shift' if the schedule type is 'Flexible'"))
        
    start_date = fields.Datetime('Start Date', required=True)
    end_date = fields.Datetime('End Date', required=True)
    grace_period = fields.Float('Grace Period', required=True)
    schedule_type = fields.Selection([('flexible', 'Flexible'), 
                                      ('normal', 'Normal'),
                                      ('coretime', 'Core-Time')], 'Schedule Type', 
                                     required=True, help="* Flexible: Flexible time\n."
                                                         "* Normal: Normal working time.\n"
                                                         "* Core-time: Core Time.")
    night_shift = fields.Boolean('Graveyard Shift', default=False)
    name = fields.Char('Name')
    employee_id = fields.Many2one('hr.employee', 'Employee', required=True)
    department_id = fields.Many2one('hr.department', 'Department',)
    employee_num = fields.Char(required=True, string="Employee Number")

    priority = fields.Integer('Priority')
    work_time_lines = fields.One2many('hr.employee_schedule.work_time.lines', 'work_time_id', 'List of Work time') 
    state = fields.Selection([('draft', 'Draft'),
                              ('approved', 'Approved'),
                              ('disapproved', 'Disapproved'),
                              ('cancelled', 'Cancelled')], default="draft", 
                              help="* Draft: Newly create record.\n"
                                   "* Approved: The work schedule is approved.\n"
                                   "* Disapproved: The work schedule is disapproved.\n"
                                   "* Cancelled: The work schedule was cancelled.")

class HREmployeeScheduleWorkTimeLines(models.Model):
    _name = "hr.employee_schedule.work_time.lines"
    _description = "Employee Work Time Lines"
    _rec_name = 'work_time_id'
    
    days_of_week = fields.Selection([('monday', 'Monday'), 
                                     ('tuesday',  'Tuesday'),
                                     ('wednesday', 'Wednesday'),
                                     ('thursday', 'Thursday'),
                                     ('friday', 'Friday'), 
                                     ('saturday', 'Saturday'),
                                     ('sunday', 'Sunday')], 'Days of Week', 
                                    required=True)
    
    earliest_check_in = fields.Float('Earliest Check-in')
    latest_check_in = fields.Float('Latest Check-in')
    time_to_render = fields.Float('Time to Render')
    break_period = fields.Float('Break Period')
    
    work_time_id = fields.Many2one('hr.employee.schedule.work_time', 'Work Time', ondelete="restrict")