from odoo import models,fields,api


class PersonalActionNotice(models.TransientModel):
    _name = 'hr.personal_action_notice'
    _description = "Personnel Action Notice"
    
    emp_no = fields.Char(string="Employee No.", related="employee_id.barcode", required=True)
    employee_id = fields.Many2one('hr.employee', 'Employee', required=True)
    efectivity_date = fields.Date(string="Effectivity Date")
    revision_no = fields.Integer(string="Revision No.")
    
    #hiring = fields.Text(string='Hiring')
    #salary_changes = fields.Text(string="Salary Changes")
    #pos_changes = fields.Text(string="Position Changes")
    #leave_abs = fields.Text(string= "Leaves Of Absences")
    #separation = fields.Text(string="Separation")
    
    dep = fields.Many2one('hr.department', string="Department")
    pos = fields.Many2one('hr.job', string="Position")
    emp_typ = fields.Many2one('hr.contract.type', string="Employment Type")
    wage = fields.Float('Salary')
    civil_status = fields.Selection([('single','Single'),
                                        ('married', 'Married'),
                                        ('widower', 'Widower'),
                                        ('divorced', 'Divorced')])
    
    to_dep = fields.Many2one('hr.department', string="Department")
    to_pos = fields.Many2one('hr.job', string="Position")
    to_emp_typ = fields.Many2one('hr.contract.type', string="Employment Type")
    to_wage = fields.Float('Salary')
    to_civil_status = fields.Selection([('single','Single'),
                                        ('married', 'Married'),
                                        ('widower', 'Widower'),
                                        ('divorced', 'Divorced')])
    
    level = fields.Integer(string="Level")
    rep_to = fields.Many2one('hr.employee', string="Reporting To")
    remarks = fields.Text(string="Remarks")
    prep_by = fields.Many2one('hr.employee', string="Prepared by:", required=True)
    rev_by = fields.Many2one('hr.employee', string="Reviewed by:", required=True)
    app_by = fields.Many2one('hr.employee', string="Approved by:", required=True)
    

    @api.onchange('emp_no')
    def onchange_emp_details(self):
       
        if self.emp_no:
            emp = self.env['hr.employee'].search([('barcode', '=', self.emp_no)], limit=1)
            
            if emp:
                
                self.rep_to = emp.parent_id and emp.parent_id.id
                self.dep = emp.department_id.id
                self.pos = emp.job_id and emp.job_id.id
                self.level = emp.job_id and emp.job_id.level
                self.wage = emp.contract_id and emp.contract_id.wage
                self.emp_typ = emp.contract_id and emp.contract_id.type_id.id
                self.civil_status = emp.marital
                
    @api.multi
    def print_report(self):
        self.ensure_one()
        data = {'ids': self.env.context.get('active_ids', []),
                'prepared_by' : self.prep_by.job_id.name,
                'revised_by' : self.rev_by.job_id.name,
                'approved_by' : self.app_by.job_id.name                
                }
    
        res = self.read()
        res = res and res[0] or {}
        data.update({'form': res})

        hr_contract = self.env['hr.contract'].search([('employee_id', '=', self.employee_id.id)], limit=1)
        if hr_contract:
            hr_contract.write({
                'department_id': self.to_dep.id,
                'job_id': self.to_pos.id,
                'type_id': self.to_emp_typ.id,
                'old_wage': self.wage,
                'wage': self.to_wage,
                'new_salary_date': self.efectivity_date,
            })
        
        hr_employee = self.env['hr.employee'].search([('employee_num', '=', self.emp_no)], limit=1)
        if hr_employee:
            hr_employee.write({
                'department_id': self.to_dep.id,
                'job_id': self.to_pos.id,
            })

        return self.env['report'].get_action(self,'hris.report_personal_action_notice', data=data)
    