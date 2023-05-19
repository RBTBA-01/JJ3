#-*- coding:utf-8 -*-

from odoo import fields,models

class HRSalaryRule(models.Model):
    _inherit = 'hr.salary.rule'
    
    net_cap_basic = fields.Boolean('Net Cap Basic', help="Basis of net cap limit percentage.")
    net_cap_total = fields.Boolean('Net Cap Total', help="Compares net cap total to basic.") 

    def get_loan_deduction(self, contract, payslip, employee):
        all_loan_type = self.env['loan.type'].search([])
        loan_type = all_loan_type.filtered(lambda l: self in l.salary_rule_ids)
        loans = self.env['employee.loan.details'].search([('loan_type', 'in', loan_type.ids),
                                                          ('employee_id', '=', employee.id),
                                                          ('state', '=', 'disburse')])
        amount = 0
        loan_amount = 0
        for install in loans.mapped('installment_lines'):
            if install.date_from >= payslip.date_from and install.date_from <= payslip.date_to:
                amount += install.total
            elif install.date_to > payslip.date_from and install.date_to <= payslip.date_to:
                amount += install.total
            elif install.date_to > payslip.date_to and install.date_from <= payslip.date_from:
                amount += install.total
        if amount > 0:
            if contract.schedule_pay == 'bi-monthly':
                loan_amount = amount/2
            elif contract.schedule_pay == 'monthly':
                loan_amount = amount
        return loan_amount*2


class HRPayslip(models.Model):
    _inherit = 'hr.payslip'
    
    net_cap = fields.Boolean('With Net Cap', help="Enables minimum take home pay monitoring")