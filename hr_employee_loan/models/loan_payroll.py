# -*- coding: utf-8 -*-

import odoo.addons.decimal_precision as dp

import time
import calendar
from datetime import datetime
from dateutil.relativedelta import relativedelta

from odoo import models, fields, api, _
from odoo.tools import DEFAULT_SERVER_DATE_FORMAT, DEFAULT_SERVER_DATETIME_FORMAT, float_compare
from odoo.exceptions import Warning, ValidationError


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    def get_installment_loan(self, emp_id, date_from, date_to=None):
        if date_to is None:
            date_to = datetime.now().strftime('%Y-%m-%d')
        # probuse added paid state and loan_repayment_method condition
        self._cr.execute("SELECT sum(o.principal_amt) from loan_installment_details as o where \
                            o.employee_id=%s \
                            AND o.state != 'paid'\
                            AND o.loan_repayment_method = 'salary'\
                            AND to_char(o.date_from, 'YYYY-MM-DD') >= %s AND to_char(o.date_from, 'YYYY-MM-DD') <= %s ",
                         (emp_id, date_from, date_to))
        res = self._cr.fetchone()
#         print "RRESSS",res
        return res and res[0] or 0.0

    def get_interest_loan(self, emp_id, date_from, date_to=None):
        if date_to is None:
            date_to = datetime.now().strftime('%Y-%m-%d')
        # probuse added paid state  and loan_repayment_method condition
        self._cr.execute("SELECT sum(o.interest_amt) from loan_installment_details as o where \
                            o.employee_id=%s \
                            AND o.state != 'paid'\
                            AND o.loan_repayment_method = 'salary'\
                            AND to_char(o.date_from, 'YYYY-MM-DD') >= %s AND to_char(o.date_from, 'YYYY-MM-DD') <= %s ",
                         (emp_id, date_from, date_to))
        res = self._cr.fetchone()
        return res and res[0] or 0.0


class LoanType(models.Model):
    _name = 'loan.type'
    _description = 'Loan Type'
    _inherit = ['mail.thread', 'ir.needaction_mixin']

    @api.multi
    def onchange_interest_payable(self, int_payble):
        if not int_payble:
            return {'value': {'interest_mode': '', 'int_rate': 0.0}}
        return {}

    state = fields.Selection(
        selection=[
            ('draft', 'Draft'),
            ('done', 'Done')],
        string='State',
        readonly=True
    )
    name = fields.Char(
        string='Name',
        required=True
    )
    code = fields.Char(
        string='Code'
    )
    int_payable = fields.Boolean(
        string='Is Interest Payable',
        default=True
    )
    interest_mode = fields.Selection(
        selection=[
            ('flat', 'Flat'),
            ('reducing', 'Reducing'),
            ('', '')],
        string='Interest Mode',
        default='flat'
    )
    int_rate = fields.Float(
        string='Rate',
        help='Put interest between 0-100 in range',
        digits=(16, 2),
        required=True
    )
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        required=False,
        readonly=True,
        default=lambda self: self.env.user.company_id
    )
    loan_interest_account = fields.Many2one(
        'account.account',
        string="Interest Account"
    )
    employee_categ_ids = fields.Many2many(
        'hr.employee.category',
        'employee_category_loan_type_rel',
        'loan_type_id',
        'category_id',
        'Employee Categories'
    )
    employee_ids = fields.Many2many(
        'hr.employee',
        'loan_type_employee_rel',
        'loan_type_id',
        'employee_id',
        "Employee's"
    )
    payment_method = fields.Selection(
        selection=[
            ('salary', 'Deduction From Payroll'),
            ('cash', 'Direct Cash/Cheque')],
        string='Repayment Method',
        default='salary'
    )
    disburse_method = fields.Selection(
        selection=[
            ('payroll', 'Through Payroll'),
            ('loan', 'Direct Cash/Cheque')],
        string='Disburse Method',
        default='loan'
    )
    loan_proof_ids = fields.Many2many(
        'loan.proof',
        string='Loan Proofs',
    )
    salary_rule_ids = fields.Many2many('hr.salary.rule', string='Salary Rules')


class LoanInstallmentDetail(models.Model):
    _name = 'loan.installment.details'
    _description = 'Loan Installment'
    _inherit = ['mail.thread', 'ir.needaction_mixin']

    @api.constrains("total")
    def validate_installment_amount(self):
        for record in self:
            if record.loan_id.state in ['disburse']:
                paid = record.loan_id.installment_lines.filtered(lambda x: x.state == 'paid')
                unpaid_lines = record.loan_id.installment_lines.filtered(lambda x: x.state == 'unpaid' and x.id != record.id)
                total_amount = record.total + sum(paid.mapped("total"))
                if total_amount > record.loan_id.final_total:
                    raise ValidationError("You can't input more than total loan Amount of ₱%s " % ('{:0,.2f}'.format(record.loan_id.final_total)))
                elif not unpaid_lines and float('{0:.2f}'.format(total_amount)) < record.loan_id.final_total and float('{0:.2f}'.format(total_amount)) > 0.00:
                    remaining_amount = record.loan_id.final_total - total_amount
                    print(float('{0:.2f}'.format(total_amount)), 'ssssssssssss')
                    raise ValidationError("Insufficient Installment Amount you still need to pay ₱%s" % ('{:0,.2f}'.format(remaining_amount)))

    @api.onchange('total')
    def generate_line_loan(self):
        loan = self._origin.loan_id.installment_lines
        paid_lines = loan.filtered(lambda x: x.state == 'paid' and x.id != self._origin.id)
        unpaid_lines = loan.filtered(lambda x: x.state == 'unpaid' and x.id != self._origin.id)
        if len(unpaid_lines):

            total_paid_installment = sum(paid_lines.mapped('total'))

            loan_amount = self.loan_id.final_total
            remaining_amount_to_pay = loan_amount - (self.total + total_paid_installment)

            need_to_pay_each_row = remaining_amount_to_pay / len(unpaid_lines)
            if need_to_pay_each_row <= 0.00:
                to_pay = 0.00
            else:
                to_pay = float('{0:.2f}'.format(need_to_pay_each_row))

            amount_all_row = self.total + total_paid_installment + (to_pay * len(unpaid_lines))
            exceed_amount = loan_amount - amount_all_row

            last_id = unpaid_lines[-1] if unpaid_lines else False

            for line in unpaid_lines:
                if last_id and line.id != last_id.id:
                    line.sudo().write({'total': to_pay})
                else:
                    line.sudo().write({'total': to_pay + exceed_amount})

    #_order = "loan_id desc"

#    @api.multi
#    @api.depends('loan_id', 'loan_id.loan_type')
#    def _check_status(self):
#        payslip_obj = self.env['hr.payslip']
#        for install in self:
#            if install.loan_id:
#                if install.loan_id.loan_type.payment_method == 'salary' and install.loan_id.state == 'disburse':
#                    payslips = payslip_obj.search([('contract_id', '=', install.loan_id.employee_id.contract_id.id),
#                                                 ('date_to', '>=', install.date_from),
#                                                 ('date_to', '<=', install.date_to)])
#                    for slip in payslips:
#                        if slip.state == 'done':
#                            for line in slip.line_ids:
#                                if line.salary_rule_id.loan_deduction:
#                                    install.check_status = True
#                                    break
#                            if install.check_status:
#                                self._cr.execute("update loan_installment_details set state='paid' where id = %s" % (install.id))
#                                break

    @api.multi
    @api.depends('loan_id', 'install_no')
    def _get_name(self):
        for install in self:
            install.name = install.loan_id and install.loan_id.name or '' + '/Install/' + str(install.install_no)

#    test_prepayment_id = fields.Many2one(
#        'loan.prepayment',
#        string='Prepayment',
#        required=False
#    )
    name = fields.Char(
        compute='_get_name',
        string='Name',
        store=True
    )
    install_no = fields.Integer(
        string='Number',
        required=True,
        readonly=True,
        states={'unpaid': [('readonly', True)]},
        help='Installment number.'
    )
    currency_id = fields.Many2one(
        'res.currency',
        string='Currency',
        required=True,
        readonly=True,
        states={'draft': [('readonly', False)]},
        default=lambda self: self.env.user.company_id.currency_id
    )
    loan_id = fields.Many2one(
        'employee.loan.details',
        string='Loan',
        readonly=True,
        states={'unpaid': [('readonly', True)]},
        required=False
    )
    date_from = fields.Date(
        string='Date From',
        #readonly=True,
        states={'unpaid': [('readonly', False)],'paid': [('readonly', True)]}
       # states={'unpaid': [('readonly', True)]}
    )
    date_to = fields.Date(
        string='Date To',
        #readonly=True,
        #states={'unpaid': [('readonly', True)]}
        states={'unpaid': [('readonly', False)],'paid': [('readonly', True)]}
    )
    principal_amt = fields.Float(
        string='Principal Amount',
        digits=(16, 2),
        readonly=True,
        #states={'paid': [('readonly', True)]}
    )
    amount_already_paid = fields.Float(
        string='Amount Already Paid',
        digits=(16, 2),
        readonly=True,
        states={'unpaid': [('readonly', False)]}
    )
    interest_amt = fields.Float(
        string='Interest Amount',
        digits=(16, 2),
        readonly=True,
        states={'unpaid': [('readonly', True)]}
    )
    paid_amount = fields.Float(
        'Paid Amount',
        digits=(16, 2),
        readonly=True,
        states={'unpaid': [('readonly', True)]}
    )
    int_payable = fields.Boolean(
        related='loan_id.int_payable',
        string='Interest Payable',
        invisible=True,
        store=True
    )
    loan_type = fields.Many2one(
        related='loan_id.loan_type',
        string='Loan Type',
        store=True
    )
    loan_repayment_method = fields.Selection(
        related='loan_type.payment_method',
        string='Loan Repayment Method',
        store=True,
    )  # probuse
    loan_state = fields.Selection(
        related='loan_id.state',
        string='Loan State',
        store=True,

    )
    total = fields.Float(
        string='EMI (Installment)',
        digits=(16, 2),
        states={'unpaid': [('readonly', False)],'paid': [('readonly', True)]},
        help='Equated monthly installments.'
    )
    employee_id = fields.Many2one(
        related='loan_id.employee_id',
        store=True,
        string='Employee',
        readonly=True
    )
    move_id = fields.Many2one(
        'account.move',
        string='Accounting Entry',
        readonly=True
    )
    int_move_id = fields.Many2one(
        'account.move',
        string='Interest Accounting Entry',
        readonly=True
    )
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        required=True,
        readonly=True,
        states={'unpaid': [('readonly', False)]},
        default=lambda self: self.env.user.company_id
    )
#    check_status = fields.Boolean(
#        compute='_check_status',
#        string='Check Status',
#        store=True
#    )
    state = fields.Selection(
        selection=[
            ('unpaid', 'Unpaid'),
            ('approve', 'Approved'),
            ('paid', 'Paid')],
        string='State',
        readonly=True,
        default='unpaid',
        track_visibility='onchange',
    )

    @api.multi
    def action_reset(self):
        #         print "==============action_reset============="
        for rec in self:
            rec.state = 'unpaid'
#         return self.write({'state':'unpaid'})

    @api.multi
    def action_approve(self):
        for rec in self:
            rec.state = 'approve'

    @api.multi
    def book_interest(self):  # todoprobuse
        move_pool = self.env['account.move']
#         period_pool = self.env['account.period']
        for installment in self:
            if installment.loan_id.loan_type.payment_method == 'cash':
                if installment.loan_id:
                    if installment.loan_id.state != 'disburse':
                        raise Warning(_('Loan is not Disbursed yet !'))
                    if installment.int_move_id:
                        raise Warning(_('Book interest entry is already generated !'))
    #             period_id = period_pool.find(installment.date_from)[0]
                timenow = time.strftime('%Y-%m-%d')
                # address_id = installment.loan_id.employee_id.address_home_id or False
                # partner_id = address_id and address_id.id or False

                # if not partner_id:
                #     raise Warning(_('Please configure Home Address for Employee !'))

                move = {
                    'narration': installment.loan_id.name,
                    'date': installment.date_from,
                    'ref': installment.install_no,
                    'journal_id': installment.loan_id.journal_id2.id,
                    #                 'period_id': period_id,
                }
#                 debit_account_id = installment.loan_id.journal_id2.default_debit_account_id
#                 if not debit_account_id:
#                     raise Warning( _('Please configure Debit/Credit accounts on the Journal %s ') % (installment.journal_id.name))
#                 debit_account_id = debit_account_id.id
                deb_interest_line = (0, 0, {
                    'name': _('Interest of loan %s') % (installment.loan_id.name),
                    'date': installment.date_from,
                    'partner_id': partner_id,
                    'account_id': installment.loan_id.employee_loan_account.id,
                    'journal_id': installment.loan_id.journal_id2.id,
                    #                     'period_id': period_id,
                    'debit': installment.interest_amt,
                    'credit': 0.0
                })

                cred_interest_line = (0, 0, {
                    'name': _('Interest of loan %s') % (installment.loan_id.name),
                    'date': installment.date_from,
                    'partner_id': partner_id,
                    'account_id': installment.loan_id.loan_type.loan_interest_account.id,
                    'journal_id': installment.loan_id.journal_id2.id,
                    #                     'period_id': period_id,
                    'credit': installment.interest_amt,
                    'debit': 0.0
                })
                move.update({'line_ids': [deb_interest_line, cred_interest_line]})
                inst_move_id = move_pool.create(move)
                installment.write({'int_move_id': inst_move_id.id})
    #             if installment.loan_id.journal_id2.entry_posted:
    #                 inst_move_id.post()
        return True

    @api.multi
    def book_interest1(self):  # ok
        move_pool = self.env['account.move']
#         period_pool = self.env['account.period']
        for installment in self:
            #            if installment.loan_id:
            #                if installment.loan_id.state == 'draft':
            #                    raise Warning(_('Loan is not confirm/Approved yet !'))
            #                if installment.int_move_id:
            #                    raise Warning(_('Book interest entry is already generated !'))
            #             period_id = period_pool.find(installment.date_from)[0]
            timenow = time.strftime('%Y-%m-%d')
            # address_id = installment.loan_id.emp_id.address_home_id or False
            # partner_id = address_id and address_id.id or False

            # if not partner_id:
            #     raise Warning(_('Please configure Home Address for Employee !'))

            move = {
                'narration': installment.loan_id.name,
                'date': installment.date_from,
                'ref': installment.install_no,
                'journal_id': installment.loan_id.journal_id2.id,
                #                 'period_id': period_id,
            }
#             debit_account_id = installment.loan_id.journal_id2.default_debit_account_id
#             if not debit_account_id:
#                 raise Warning( _('Please configure Debit/Credit accounts on the Journal %s ') % (installment.loan_id.journal_id2.name))
#             debit_account_id = debit_account_id.id
            if not installment.loan_id.employee_loan_account:
                raise Warning(_('Please configure Employee account.'))
            deb_interest_line = (0, 0, {
                'name': _('Interest of loan %s') % (installment.loan_id.name),
                'date': installment.date_from,
                'partner_id': partner_id,
                'account_id': installment.loan_id.employee_loan_account.id,
                'analytic_account_id': installment.loan_id.account_analytic_id.id or False,
                'journal_id': installment.loan_id.journal_id2.id,
                #                     'period_id': period_id,
                'debit': installment.interest_amt,
                'credit': 0.0
            })
            cred_interest_line = (0, 0, {
                'name': _('Interest of loan %s') % (installment.loan_id.name),
                'date': installment.date_from,
                'partner_id': partner_id,
                'account_id': installment.loan_id.loan_type.loan_interest_account.id,
                'journal_id': installment.loan_id.journal_id2.id,
                #                     'period_id': period_id,
                'credit': installment.interest_amt,
                'debit': 0.0
            })
            move.update({'line_ids': [deb_interest_line, cred_interest_line]})
            inst_move_id = move_pool.create(move)
            installment.write({'int_move_id': inst_move_id.id})
#             if installment.loan_id.journal_id2.entry_posted:#todoprobuse
#                 inst_move_id.post()
#             inst_move_id.post()
        return True

    @api.multi
    def pay_installment1(self):  # ok
        ctx = dict(self._context or {})
        ctx.update(recompute=True)
        move_pool = self.env['account.move']
#         period_pool = self.env['account.period']
        for installment in self:
            if installment.loan_id.loan_type.payment_method == 'cash':
                #                 period_id = period_pool.find(installment.date_from)[0]
                timenow = time.strftime('%Y-%m-%d')
                # address_id = installment.loan_id.emp_id.address_home_id or False
                # partner_id = address_id and address_id.id or False

                # if not partner_id:
                #     raise Warning(_('Please configure Home Address for Employee !'))

                move = {
                    'narration': installment.loan_id.name,
                    'date': installment.date_from,
                    'ref': installment.install_no,
                    'journal_id': installment.loan_id.journal_id1.id,
                    #                     'period_id': period_id,
                }
                if not installment.loan_id.journal_id1.default_debit_account_id:
                    raise Warning(_('Please configure Debit/Credit accounts on the Journal %s ') % (installment.loan_id.journal_id1.name))
                if not installment.loan_id.employee_loan_account:
                    raise Warning(_('Please give employee account.'))
                debit_line = (0, 0, {
                    'name': _('EMI of loan %s') % (installment.loan_id.name),
                    'date': installment.date_from,
                    'partner_id': partner_id,
                    'account_id': installment.loan_id.journal_id1.default_debit_account_id.id,
                    'journal_id': installment.loan_id.journal_id1.id,
                    #                         'period_id': period_id,
                    'debit': installment.total,
                    'credit': 0.0,
                })
                credit_line = (0, 0, {
                    'name': _('EMI of loan %s') % (installment.loan_id.name),
                    'date': installment.date_from,
                    'partner_id': partner_id,
                    'account_id': installment.loan_id.employee_loan_account.id,
                    'journal_id': installment.loan_id.journal_id1.id,
                    #                         'period_id': period_id,
                    'debit': 0.0,
                    'analytic_account_id': installment.loan_id.account_analytic_id.id or False,
                    'credit': installment.total,
                })
                move.update({'line_ids': [debit_line, credit_line]})
                move_id = move_pool.create(move)
                installment.write({'state': 'paid', 'move_id': move_id.id})
#                 if installment.loan_id.journal_id1.entry_posted:
#                     move_id.post()#todoprobuse
#                 move_id.post()
#                self.pool.get('employee.loan.details').compute_installments(cr, uid, [installment.loan_id.id], context=context)
                ctx.pop('recompute')
        return True

    @api.multi
    def pay_installment(self):  # ok #probusetodo when call from form view of opening balance
        ctx = dict(self._context or {})
        move_pool = self.env['account.move']
#         period_pool = self.env['account.period']
        for installment in self:
            if installment.loan_id.state != 'disburse':
                raise Warning(_('Loan is not Disbursed yet !'))
            if installment.loan_id.loan_type.payment_method == 'cash':
                #                 period_id = period_pool.find(installment.date_from)[0]
                timenow = time.strftime('%Y-%m-%d')
                # address_id = installment.loan_id.employee_id.address_home_id or False
                # partner_id = address_id and address_id.id or False

                # if not partner_id:
                #     raise Warning(_('Please configure Home Address for Employee !'))

                move = {
                    'narration': installment.loan_id.name,
                    'date': installment.date_from,
                    'ref': installment.install_no,
                    'journal_id': installment.loan_id.journal_id1.id,
                    #                     'period_id': period_id,
                }
                if not installment.loan_id.journal_id1.default_debit_account_id:
                    raise Warning(_('Please configure Debit/Credit accounts on the Journal %s ') % (self.journal_id1.name))
                debit_line = (0, 0, {
                    'name': _('EMI of loan %s') % (installment.loan_id.name),
                    'date': installment.date_from,
                    'partner_id': partner_id,
                    'account_id': installment.loan_id.journal_id1.default_debit_account_id.id,
                    'journal_id': installment.loan_id.journal_id1.id,
                    #                         'period_id': period_id,
                    'debit': installment.total,
                    'credit': 0.0,
                })
                credit_line = (0, 0, {
                    'name': _('EMI of loan %s') % (installment.loan_id.name),
                    'date': installment.date_from,
                    'partner_id': partner_id,
                    'account_id': installment.loan_id.employee_loan_account.id,
                    'journal_id': installment.loan_id.journal_id1.id,
                    #                         'period_id': period_id,
                    'debit': 0.0,
                    'credit': installment.total,
                })
                move.update({'line_ids': [debit_line, credit_line]})
                move_id = move_pool.create(move)
                installment.write({'state': 'paid', 'move_id': move_id.id})
#                 if installment.loan_id.journal_id.entry_posted:#todoprobuse
#                     move_id.post()
#                 move_id.post()
                installment.loan_id.compute_installments()
                if ctx.get('recompute'):
                    ctx.pop('recompute')
            else:
                if installment.paid_amount <= installment.total:
                    installment.write({'state': 'paid'})
        return True


class EmployeeLoanDetails(models.Model):
    _name = "employee.loan.details"
    _description = "Employee Loan"
    _inherit = ['mail.thread', 'ir.needaction_mixin']
    _order = "name desc"

#     def _get_loan_rec(self, cr, uid, ids, context=None):
#         result = []
#         for line in self.pool.get('loan.installment.details').browse(cr, uid, ids, context=context):
#             result.append(line.loan_id.id)
#         return result

    def _update_installment_loans(self, amount, ids):
        for lines in self.installment_lines.filtered(lambda x: x.id in ids):
            lines.total = amount

    @api.multi
    @api.depends('installment_lines', 'principal_amount', 'int_rate', 'duration', 'installment_lines.state')
    def _cal_amount_all(self):
        for rec in self:
            if rec.int_payable:
                if rec.interest_mode == 'flat':
                    rec.total_interest_amount = self.flat_rate_method(rec.principal_amount, rec.int_rate, rec.duration)
                else:
                    values = self.reducing_balance_method(rec.principal_amount, rec.int_rate, rec.duration)
                    for key, value in values.iteritems():
                        rec.total_interest_amount += value['interest_comp']

            for payment in rec.installment_lines:
                if payment.state == 'paid':
                    rec.total_amount_paid += payment.total

            rec.final_total = rec.principal_amount + rec.total_interest_amount
            rec.total_amount_due = rec.final_total - rec.total_amount_paid

    @api.multi
    @api.depends('loan_policy_ids')
    def _calc_max_loan_amt(self):
        for rec in self:
            for policy in rec.loan_policy_ids:
                if policy.policy_type == 'maxamt':
                    if policy.max_loan_type == 'basic':
                        if rec.employee_id.contract_id.wage:
                            rec.max_loan_amt = rec.employee_id.contract_id.wage * policy.policy_value / 100
                    elif policy.max_loan_type == 'gross':
                        rec.max_loan_amt = rec.employee_gross * policy.policy_value / 100
                    else:
                        rec.max_loan_amt = policy.policy_value

    @api.one
    def _check_multi_loan(self, employee):
        allow_multiple_loans = employee.allow_multiple_loan
        for categ in employee.category_ids:
            if categ.allow_multiple_loan:
                allow_multiple_loans = categ.allow_multiple_loan
                break
        return allow_multiple_loans

    @api.multi
    @api.depends('loan_type')
    def _get_loan_values(self):
        res = {}
        for rec in self:
            allowed_employees = []
            for categ in rec.loan_type.employee_categ_ids:
                allowed_employees += map(lambda x: x.id, categ.employee_ids)
            allowed_employees += map(lambda x: x.id, rec.loan_type.employee_ids)
            if rec.employee_id.id in allowed_employees:
                rec.int_rate = rec.loan_type.int_rate
                rec.interest_mode = rec.loan_type.interest_mode
                rec.int_payable = rec.loan_type.int_payable

    @api.model
    def flat_rate_method(self, principal, rate, duration):
        return ((principal * rate) / 100)

    @api.depends('loan_type')
    def _compute_proof_loan(self):
        for rec in self:
            rec.loan_proof_ids = rec.loan_type.loan_proof_ids

    @api.model
    def reducing_balance_method(self, p, r, n):
        # Determine the interest rate on the loan, the length of the loan and the amount of the loan
        res = {}
        for i in range(0, n):
            step_1_p = p  # principal amount at the beginning of each period
            step_2_r_m = r / (12 * 100.00)  # interest rate per month
            step_3_r_m = 1 + step_2_r_m  # add 1 to interest rate per month
            step_4 = step_3_r_m ** (n - i)  # Raise the step_2_r_m to the power of the number of payments required on the loan
            step_5 = step_4 - 1  # minus 1 from step_4
            step_6 = step_2_r_m / step_5  # Divide the interest rate per month(step_2_r_m) by the step_5
            step_7 = step_6 + step_2_r_m  # Add the interest rate per month to the step_6
            step_8_EMI = round(step_7 * step_1_p, 2)  # Total EMI to pay month
            step_9_int_comp = round(step_1_p * step_2_r_m, 2)  # Total Interest component in EMI
            step_10_p_comp = round(step_8_EMI - step_9_int_comp, 2)  # Total principal component in EMI
            p -= step_10_p_comp  # new principal amount
            res[i] = {'emi': step_8_EMI,
                      'principal_comp': step_10_p_comp,
                      'interest_comp': step_9_int_comp
                      }
        return res

#    @api.multi
#    def _check_status(self):
#        payslip_obj = self.env['hr.payslip']
#        for loan in self:
#            if loan.loan_type.disburse_method == 'payroll' and loan.state == 'approved':
#                payslips = payslip_obj.search([('contract_id', '=', loan.employee_id.contract_id.id),
#                                             ('date_to', '>=', loan.date_approved),
#                                             ('date_from', '<=', loan.date_approved)])
#                for slip in payslips:
#                    if slip.state == 'done':
#                        for line in slip.line_ids:
#                            if line.salary_rule_id.loan_allowance:
#                                loan.check_status = True
#                                break
#                        if loan.check_status:
#                            self._cr.execute("update employee_loan_details set state='disburse' where id = %s" % (loan.id))
#                            break
    @api.model
    def _employee_get(self):
        ids = self.env['hr.employee'].search([('user_id', '=', self._uid)], limit=1)
        if ids:
            return ids

    name = fields.Char(
        string='Number',
        readonly=True,
        copy=False
    )
    employee_id = fields.Many2one(
        'hr.employee',
        string='Employee',
        required=True,
        readonly=True,
        states={'draft': [('readonly', False)]},
        default=_employee_get
    )
    department_id = fields.Many2one(
        'hr.department',
        string="Department",
        states={'paid': [('readonly', True)], 'disburse': [('readonly', True)], 'approved': [('readonly', True)]},
    )
    date_applied = fields.Date(
        string='Applied Date',
        required=True,
        readonly=False,
        states={'paid': [('readonly', True)], 'disburse': [('readonly', True)], 'approved': [('readonly', True)]},
        default=fields.Date.context_today
    )
    date_approved = fields.Date(
        string='Approved Date',
        readonly=True,
        copy=False
    )
    date_repayment = fields.Date(
        string='Repayment Date',
        readonly=False,
        states={'paid': [('readonly', True)], 'disburse': [('readonly', True)], 'approved': [('readonly', True)]},
        copy=False
    )
    date_disb = fields.Date(
        string='Disbursement Date',
        readonly=False,
        states={'paid': [('readonly', True)], 'disburse': [('readonly', True)]}
    )
    loan_type = fields.Many2one(
        'loan.type',
        string='Loan Type',
        required=True,
        readonly=True,
        states={'draft': [('readonly', False)]}
    )
    duration = fields.Integer(
        string='Duration(Months)',
        required=True,
        readonly=True,
        states={'draft': [('readonly', False)]}
    )
    loan_policy_ids = fields.Many2many(
        'loan.policy',
        'loan_policy_rel',
        'policy_id',
        'loan_id',
        string="Active Policies",
        states={'disburse': [('readonly', True)]}
    )
    int_payable = fields.Boolean(
        compute='_get_loan_values',
        #         multi='type',
        string='Is Interest Payable',
        store=True,
        default=False
    )
    interest_mode = fields.Selection(
        compute='_get_loan_values',
        selection=[('flat', 'Flat'), ('reducing', 'Reducing'), ('', '')],
        #         multi='type',
        string='Interest Mode',
        store=True,
        default=''
    )
    int_rate = fields.Float(
        compute='_get_loan_values',
        string='Rate',
        multi='type',
        help='Interest rate between 0-100 in range',
        digits=(16, 2),
        store=True
    )
    principal_amount = fields.Float(
        string='Principal Amount',
        required=True,
        readonly=True,
        states={'draft': [('readonly', False)]}
    )
    employee_gross = fields.Float(
        string='Gross Salary',
        help='Employee Gross Salary from Payslip if payslip is not available please enter value manually.',
        required=False,
        readonly=True,
        states={'draft': [('readonly', False)]}
    )
    final_total = fields.Float(
        compute='_cal_amount_all',
        string='Total Loan',
        store=True
    )
    total_amount_paid = fields.Float(
        compute='_cal_amount_all',
        #         multi="calc",
        string='Received From Employee',
        store=True
    )
    total_amount_due = fields.Float(
        compute='_cal_amount_all',
        #         multi="calc",
        help='Remaining Amount due.',
        string='Balance on Loan',
        store=True
    )
    total_interest_amount = fields.Float(
        compute='_cal_amount_all',
        string='Total Interest on Loan',
        store=True
    )
    max_loan_amt = fields.Float(
        compute='_calc_max_loan_amt',
        store=True,
        string='Max Loan Amount'
    )
    installment_lines = fields.One2many(
        'loan.installment.details',
        'loan_id',
        'Installments',
        copy=False
    )
    company_id = fields.Many2one(
        'res.company',
        'Company',
        required=True,
        readonly=True,
        states={'draft': [('readonly', False)]},
        default=lambda self: self.env.user.company_id
    )
    currency_id = fields.Many2one(
        'res.currency',
        string='Currency',
        required=True,
        readonly=True,
        states={'draft': [('readonly', False)]},
        default=lambda self: self.env.user.company_id.currency_id
    )
    user_id = fields.Many2one(
        'res.users',
        default=lambda self: self.env.user,
        string='User',
        readonly=True,
        required=True,
    )
    employee_loan_account = fields.Many2one(
        'account.account',
        string="Employee Account",
        readonly=False,
        states={'disburse': [('readonly', True)]}
    )
    journal_id = fields.Many2one(
        'account.journal',
        string='Disburse Journal',
        help='Journal related to loan for Accounting Entries',
        required=False,
        readonly=False,
        states={'disburse': [('readonly', True)]}
    )
    journal_id1 = fields.Many2one(
        'account.journal',
        string='Repayment Board Journal',
        required=False,
        readonly=False,
        states={'close': [('readonly', True)]}
    )
    journal_id2 = fields.Many2one(
        'account.journal',
        string='Interest Journal',
        required=False,
        readonly=False,
    )
    move_id = fields.Many2one(
        'account.move',
        string='Accounting Entry',
        readonly=True,
        help='Accounting Entry once loan has been given to employee',
        copy=False
    )
    loan_proof_ids = fields.Many2many(
        'loan.proof',
        compute='_compute_proof_loan',
        string='Loan Proofs',
    )
#    check_status = fields.Boolean(
#        compute='_check_status',
#        string='Check Status',
#        store=True
#    )
    state = fields.Selection(
        selection=[
            ('draft', 'Draft'),
            ('applied', 'Applied'),
            ('approved', 'Approved'),
            ('paid', 'Paid'),
            ('disburse', 'Disbursed'),
            ('rejected', 'Rejected'),
            ('cancel', 'Cancelled')],
        string='State',
        readonly=True,
        copy=False,
        default='draft',
        track_visibility='onchange',
    )
    notes = fields.Text(
        string='Note'
    )

#     @api.one
#     def copy(self, default=None):
#         print "--------copy-------------"
#         if not default:
#             default = {}
#         default.update({
#             'installment_lines': [],
#             'date_approved': False,
#             'date_repayment': False,
#             'name':False,
#             'move_id':False,
#             'state': 'draft'
#         })
#         return super(EmployeeLoanDetails, self).copy(default)

    @api.multi
    def onchange_loan_type(self, loan_type, employee):
        if loan_type:
            if not employee:
                raise Warning(_('Please specify Employee.'))
            loan_type = self.env['loan.type'].browse(loan_type)
            employee_obj = self.env['hr.employee'].browse(employee)
            allowed_employees = []
            for categ in loan_type.employee_categ_ids:
                allowed_employees += map(lambda x: x.id, categ.employee_ids)
            allowed_employees += map(lambda x: x.id, loan_type.employee_ids)
            if employee not in allowed_employees:
                raise Warning(_('%s  does not Qualify for %s ') % (employee_obj.name, loan_type.name))
        return {}

    @api.multi
    def onchange_employee_id(self, employee):
        if not employee:
            return {'value': {'loan_policy_ids': []}}
        employee_obj = self.env['hr.employee'].browse(employee)
        policies_on_categ = []
        policies_on_empl = []
        for categ in employee_obj.category_ids:
            if categ.loan_policy:
                policies_on_categ += map(lambda x: x.id, categ.loan_policy)
        if employee_obj.loan_policy:
            policies_on_empl = map(lambda x: x.id, employee_obj.loan_policy)
        domain = [('employee_id', '=', employee), ('contract_id', '=', employee_obj.contract_id.id), ('code', '=', 'GROSS')]
        line_ids = self.env['hr.payslip.line'].search(domain)
        department_id = employee_obj.department_id.id or False
#         print 'department_id==========',department_id

        # address_id = employee_obj.address_home_id or False
        # if not address_id:
        #     raise Warning(_('There is no home/work address defined for employee : %s ') % (_(employee_obj.name)))
        # partner_id = address_id and address_id.id or False
        # if not partner_id:
        #     raise Warning(_('There is no partner defined for employee : %s ') % (_(employee_obj.name)))
        gross_amount = 0.0
        if line_ids:
            #             line = self.env['hr.payslip.line'].browse(line_ids)[0]
            line = line_ids[0]
            gross_amount = line.amount

        return {'value': {'department_id': department_id,
                          'loan_policy_ids': list(set(policies_on_categ + policies_on_empl)),
                          'employee_gross': gross_amount,
                        #   'employee_loan_account': address_id.property_account_receivable_id.id or False,
                          }}

    # probuse override to fix issue of  when we apply for a loan first loan policy is selected and when we save loan request the loan policy get removed/disappeared and we have to select loan policy again
    @api.model
    def create(self, vals):
        if vals.get('employee_id', False):
            employee_id = vals['employee_id']
            employee = self.env['hr.employee'].sudo().browse(employee_id)

            policies_on_categ = []
            policies_on_empl = []
            for categ in employee.category_ids:
                if categ.loan_policy:
                    policies_on_categ += map(lambda x: x.id, categ.loan_policy)
            if employee.loan_policy:
                policies_on_empl = map(lambda x: x.id, employee.loan_policy)

            loan_policy_ids = list(set(policies_on_categ + policies_on_empl))
            vals.update({'loan_policy_ids': [(6, 0, loan_policy_ids)]})
        return super(EmployeeLoanDetails, self).create(vals)

    # checks for same loan type
    # @api.constrains('state')
    # def check_loan_state(self):
    #     loans = self.env['employee.loan.details'].search([])
    #     for rec in self:
    #         if len(loans.filtered(lambda l: l.employee_id == rec.employee_id and (l.state == 'approved' or l.state == 'disburse') and l.loan_type == rec.loan_type)) > 1:
    #             raise ValidationError(_("You cannot apply for multiple loan for same loan type!!"))

    @api.multi
    def action_applied(self):
        for loan in self:
            self.onchange_loan_type(loan.loan_type.id, loan.employee_id.id)
            msg = ''
            if loan.principal_amount <= 0.0:
                msg += 'Principal Amount\n '
            if loan.int_payable and loan.int_rate <= 0.0:
                msg += 'Interest Rate\n '
            if loan.duration <= 0.0:
                msg += 'Duration of Loan'
            if msg:
                raise Warning(_('Please Enter values greater then zero:\n %s ') % (msg))
            status = self.check_employee_loan_qualification(loan)
            if not isinstance(status, bool):
                raise Warning(_('Loan Policies not satisfied :\n %s ') % (_(status)))
            seq_no = self.env['ir.sequence'].get('employee.loan.details')
#             self.write({'state':'applied', 'name':seq_no})
            loan.state = 'applied'
            loan.name = seq_no
        return True

    @api.multi
    def action_cancel(self):
        for rec in self:
            rec.state = 'cancel'
#         return self.write({'state':'cancel'})

    @api.model
    def check_employee_loan_qualification(self, loan_obj):
        loan_date_today = loan_obj.date_applied
        loan_date_today_obj = datetime.strptime(loan_date_today, DEFAULT_SERVER_DATE_FORMAT)
        msg = 'Mr./Mrs. %s does not meet following Loan policies:' % (loan_obj.employee_id.name)
        qualified = True
        allow_multiple_loan = self._check_multi_loan(loan_obj.employee_id)
        allow_multiple_loan = filter(None, allow_multiple_loan)  # probuse
        if loan_obj.employee_id.loan_defaulter:
            msg += '\n Blacklisted: You are Blacklisted as loan defaulter and hence you cannot apply for a new loan !'
            qualified = False
            
        # if not allow_multiple_loan:
        #     loans_list = self.search([('state', '=', 'approved'), ('employee_id', '=', loan_obj.employee_id.id), ('loan_type', '=', self.loan_type.id)], order='date_applied asc')
        #     if len(loans_list):
        #         msg += '\n Multiple loan: Multiple loan is not allowed !'
                # qualified = False

        if allow_multiple_loan:
            qualified = True
        else:
            qualified = True

        for policy in loan_obj.loan_policy_ids:
            if policy.policy_type == 'maxamt':
                if loan_obj.max_loan_amt > 0.0 and loan_obj.principal_amount > loan_obj.max_loan_amt:
                    qualified = False
                    msg += '\n %s :Loan amount is > %s ' % (policy.name, loan_obj.max_loan_amt)

#             if policy.policy_type == 'loan_gap' and not allow_multiple_loan: #probuse
            if policy.policy_type == 'loan_gap' and allow_multiple_loan:  # probuse
                loans_list = self.search([('state', '=', 'disburse'), ('employee_id', '=', loan_obj.employee_id.id)], order='date_applied asc')
                if loans_list:
                    #                     last_loan = self.browse(loans_list[0]) #probuse
                    last_loan = loans_list[0]  # probuse
                    last_loan_date = last_loan.date_applied
                    last_loan_date_obj = datetime.strptime(last_loan_date, DEFAULT_SERVER_DATE_FORMAT)
                    diff = last_loan_date_obj + relativedelta(months=int(policy.policy_value))
                    if diff > loan_date_today_obj:
                        qualified = False
                        msg += '\n %s :\n\t\t Last loan date: %s \n\t\tGap required(months) : %s \n\t\tcan apply on/after: %s' \
                            % (policy.name, last_loan_date, policy.policy_value, diff.strftime('%Y-%m-%d'))
            if policy.policy_type == 'eligible_duration':
                contract_date = loan_obj.employee_id.contract_id.date_start
                contract_date_obj = datetime.strptime(contract_date, DEFAULT_SERVER_DATE_FORMAT)
                actual_date = contract_date_obj + relativedelta(months=int(policy.policy_value))
                if actual_date > loan_date_today_obj:
                    qualified = False
                    msg += '\n %s :\n\t\tContract date: %s  \n\t\tGap required(months):%s \n\t\tcan apply on/after: %s'\
                        % (policy.name, contract_date, policy.policy_value, actual_date.strftime('%Y-%m-%d'))
        if not qualified:
            return msg
        return qualified

    @api.multi
    def compute_installments(self):
        for loan in self:
            if not len(loan.installment_lines):
                self.create_installments(loan)
            elif self._context.get('recompute') and loan.int_payable:
                access_payment = 0.0
                duration_left = 0
                prin_amt_received = 0.0
                total_acc_pay = 0.0
                for install in loan.installment_lines:
                    access_payment += round(install.total - (install.principal_amt + install.interest_amt), 2)
                    if install.state in ('paid', 'approve'):
                        prin_amt_received += round(install.principal_amt, 2)
                        continue
                    duration_left += 1
                total_acc_pay = loan.duration - duration_left
                new_p = round(loan.principal_amount - round(prin_amt_received, 2) - round(access_payment, 2))
                if loan.interest_mode == 'reducing':
                    reducing_val = self.reducing_balance_method(new_p, loan.int_rate, duration_left)
                if loan.interest_mode == 'flat':
                    interest_amt = 0.0
                    principal_amt = new_p / duration_left
                    if loan.int_payable:
                        interest_amt = self.flat_rate_method(new_p, loan.int_rate, duration_left) / duration_left
                    total = principal_amt + interest_amt
                cnt = -1
                for install in loan.installment_lines:
                    cnt += 1
                    if install.state in ('paid', 'approve'):
                        continue
                    if loan.interest_mode == 'reducing':
                        principal_amt = reducing_val[cnt - total_acc_pay]['principal_comp']
                        if loan.int_payable:
                            interest_amt = reducing_val[cnt - total_acc_pay]['interest_comp']
                        total = principal_amt + interest_amt
                    install.write({'principal_amt': principal_amt,
                                   'interest_amt': interest_amt,
                                   'total': total})
            else:
                # this is to reload the values
                loan.write({})
                for install in loan.installment_lines:
                    install.write({})
        return True


    @api.model
    def compare_payroll_period(self, installment_date):
        payroll_period_lines = self.env['hr.payroll.period_line']
        domain = [('start_date', '<=', installment_date), ('end_date', '>=', installment_date)]
        period_line_records = payroll_period_lines.search(domain)

        if not period_line_records:
            raise Warning(_("No active Payroll Period!"))
        else:   
            for period_line in period_line_records:
                return period_line.start_date


    @api.model
    def create_installments(self, loan):
        date_approved = time.strftime(DEFAULT_SERVER_DATE_FORMAT)
        if not loan.date_disb:
            raise Warning(_('Please give disbursement date.'))
        else:
            date_disb = loan.date_disb

        # MANIPULATE date_approved_obj and compare to active payroll period    
        date_approved_obj = datetime.strptime(date_disb, DEFAULT_SERVER_DATE_FORMAT)

        installment_date= datetime.strptime(self.compare_payroll_period(date_approved_obj), DEFAULT_SERVER_DATE_FORMAT)

        installment_obj = self.env['loan.installment.details']
        day = installment_date.day
        month = installment_date.month 
        year = installment_date.year
        per_cutoff_duration = loan.duration * 2
        interest_amt = 0.0
        principal_amt = loan.principal_amount / (per_cutoff_duration or 1.0)
        if loan.int_payable:
            interest_amt = loan.total_interest_amount / (per_cutoff_duration or 1.0)
        total = principal_amt + interest_amt
        if loan.interest_mode == 'reducing':
            reducing_val = self.reducing_balance_method(loan.principal_amount, loan.int_rate, per_cutoff_duration)
 
        for install_no in range(0, per_cutoff_duration):
            date_from = datetime(year, month, day)
            date_to = (date_from + relativedelta(weeks=2, days=+1))

            # To correct dates caused by difference of month days
            if date_from.month == 2 and date_to.month != 2:
                date_from_days = calendar.monthrange(date_from.year, date_from.month)
                if date_from_days == 28:
                    date_to = date_to + relativedelta(days=+2)
                elif date_from_days == 29:
                    date_to = date_to + relativedelta(days=+1)
            else:
                if date_from.month == date_to.month:
                    date_to = date_to + relativedelta(days=-1)
                elif date_from.month != date_to.month:
                    date_from_days = calendar.monthrange(date_from.year, date_from.month)[1]
                    date_to_days = calendar.monthrange(date_to.year, date_to.month)[1]
                    if date_to_days == 31 and date_to_days != date_from_days:
                        date_to = date_to + relativedelta(days=-1)
            
            if loan.interest_mode == 'reducing':
                principal_amt = reducing_val[install_no]['principal_comp']
                if loan.int_payable:
                    interest_amt = reducing_val[install_no]['interest_comp']
                total = principal_amt + interest_amt
            installment_line = {'install_no': install_no + 1,
                                'date_from': date_from.strftime('%Y-%m-%d'),
                                'date_to': date_to.strftime('%Y-%m-%d'),
                                'principal_amt': principal_amt,
                                'interest_amt': interest_amt,
                                'total': total,
                                'loan_id': loan.id
                                }
            date_to = date_to + relativedelta(days=+1)
            day, month, year = date_to.day, date_to.month, date_to.year
            installment_obj.create(installment_line)
        return True

    @api.multi
    def action_disburse(self):
        move_pool = self.env['account.move']
#         period_pool = self.env['account.period']
        for loan in self:
            if loan.loan_type.disburse_method == 'payroll':
                loan.write({'state': 'disburse'})
                return True
            vals = {}
#             period_id = period_pool.find(loan.date_applied)[0]
            timenow = time.strftime('%Y-%m-%d')
            # address_id = loan.employee_id.address_home_id or False
            # partner_id = address_id and address_id and address_id.id or False

            # if not partner_id:
            #     raise Warning(_('Please configure Home Address On Employee.'))

            move = {
                'narration': loan.name,
                'date': loan.date_disb,
                'ref': loan.name,
                'journal_id': loan.journal_id.id,
                #                 'period_id': period_id.id,
            }

            credit_account_id = loan.journal_id.default_credit_account_id
            if not loan.journal_id:
                raise Warning(_('Please configure Disburse Journal.'))
            if not credit_account_id:
                raise Warning(_('Please configure Debit/Credit accounts on the Journal %s ') % (loan.journal_id.name))
            credit_account_id = credit_account_id.id
            debit_account_id = loan.employee_loan_account.id or False
            if not debit_account_id:
                raise Warning(_('Please configure debit account of employee'))

            debit_line = (0, 0, {
                'name': _('Loan of %s') % (loan.employee_id.name),
                'date': loan.date_disb,
                'partner_id': partner_id,
                'account_id': debit_account_id,
                'journal_id': loan.journal_id.id,
                #                     'period_id': period_id.id,
                'debit': loan.principal_amount,
                'credit': 0.0,
            })
            credit_line = (0, 0, {
                'name': _('Loan of %s') % (loan.employee_id.name),
                'date': loan.date_disb,
                'partner_id': partner_id,
                'account_id': credit_account_id,
                'journal_id': loan.journal_id.id,
                #                     'period_id': period_id.id,
                'debit': 0.0,
                'credit': loan.principal_amount,
            })
            move.update({'line_ids': [debit_line, credit_line]})
            move_id = move_pool.create(move)
            date_disb = time.strftime(DEFAULT_SERVER_DATE_FORMAT)
            if not loan.date_disb:
                vals.update(state='disburse', move_id=move_id.id, date_disb=date_disb)
            else:
                vals.update(state='disburse', move_id=move_id.id)
            loan.write(vals)
#             if loan.journal_id.entry_posted:#todoprobuse
#             move_id.post()
        return True

    @api.multi
    def action_approved(self):
        date_approved = time.strftime(DEFAULT_SERVER_DATE_FORMAT)
        date_approved_obj = datetime.strptime(date_approved, DEFAULT_SERVER_DATE_FORMAT)
        for loan in self:
            vals = {}
            if not loan.date_approved:
                vals.update(
                    date_approved=date_approved,
                    state='approved')
            else:
                vals.update(state='approved')

            self.write(vals)
        return True

    @api.multi
    def action_rejected(self):
        #         print "---------------action_rejected------"
        for rec in self:
            rec.state = 'rejected'
#         return self.write({'state':'rejected'})

    @api.multi
    def action_paid(self):
        for rec in self:
            rec.state = 'paid'

    @api.multi
    def action_reset(self):
        #         print "-----------action_reset----------"
        #         self.write({'state':'draft'})
        for rec in self:
            rec.state = 'draft'
        from openerp import workflow
        workflow.trg_delete(self._uid, self._name, self.id, self._cr)
        workflow.trg_create(self._uid, self._name, self.id, self._cr)
#        self.workflow_delete()
#        self.workflow_create()
        return True


class LoanPloicy(models.Model):
    _name = "loan.policy"
    _description = "Loan Policy Details"

    name = fields.Char(
        'Name',
        required=True
    )
    code = fields.Char(
        'Code'
    )
    employee_categ_ids = fields.Many2many(
        'hr.employee.category',
        'employee_category_policy_rel_loan',
        'policy_id',
        'category_id',
        'Employee Categories'
    )
    employee_ids = fields.Many2many(
        'hr.employee',
        'policy_employee_rel',
        'policy_id',
        'employee_id',
        "Employee's"
    )
    company_id = fields.Many2one(
        'res.company',
        'Company',
        required=True,
        default=lambda self: self.env.user.company_id
    )
    policy_type = fields.Selection(
        selection=[
            ('maxamt', 'Max Loan Amount'),
            ('loan_gap', 'Gap between two loans'),
            ('eligible_duration', 'Qualifying Period'),
            ('', '')],
        string='Policy Type',
        required=True,
    )
    state = fields.Selection(
        selection=[
            ('draft', 'Draft'),
            ('done', 'Done')],
        string='State',
        readonly=True
    )
    max_loan_type = fields.Selection(
        selection=[
            #('basic', 'Basic Salary'),
            #('gross', 'Gross Salary'),
            ('fixed', 'Fix Amount'),
            ('', '')],
        string='Basis',
        help='As a percentage of Basic/Gross Salary or as a fixed amount',
    )
    policy_value = fields.Float(
        string='Value',
        help='If policy type is Max Loan Amount and Basis is Fixed Amount, then set value as a fixed amount, ie maximum loan that can be taken. \n If policy type is Gap between two loans or Qualifying Period, then set value as a number of months.'
    )


class HrEmployeeCategory(models.Model):
    _inherit = "hr.employee.category"
    _description = "Employee Category"

    loan_policy = fields.Many2many(
        'loan.policy',
        'employee_category_policy_rel_loan',
        'category_id',
        'policy_id',
        string='Loan Policies'
    )
    allow_multiple_loan = fields.Boolean(
        string='Allow Multiple Loans'
    )


class HrEmployee(models.Model):
    _inherit = "hr.employee"
    _description = "Employee"

    loan_policy = fields.Many2many(
        'loan.policy',
        'policy_employee_rel',
        'employee_id',
        'policy_id',
        string='Loan Policies'
    )
    allow_multiple_loan = fields.Boolean(
        string='Allow Multiple Loans'
    )
    loan_defaulter = fields.Boolean(
        string='Loan Defaulter'
    )
    loan_ids = fields.One2many(
        'employee.loan.details',
        'employee_id',
        string='Loans Details',
        readonly=True,
        ondelete="cascade"
    )


class HrSalaryRule(models.Model):
    _inherit = 'hr.salary.rule'

#    loan_allowance = fields.Boolean(
#        string='Is Loan Allowance'
    # )
    # loan_deduction = fields.Boolean(
    #    string='Is Loan Deduction'
    # )

# class LoanPrepayment(models.Model):
#     _name = 'loan.prepayment'
#     _description = 'Loan Prepayment'
#     _inherit = ['mail.thread']
#
#     @api.multi
#     def validate(self):
#         for a in self:
#             pass
#             a.state = 'open1'
# #         return self.write({'state':'open1'})
#
#     @api.multi
#     def validate1(self):
#         for a in self:
#             pass
#             a.state = 'open'
# #         return self.write({'state':'open'})
#
#     @api.multi
#     def set_to_close(self):
#         for rec in self:
#             rec.state = 'close'
# #         return self.write({'state': 'close'})
#
#     @api.multi
#     def set_to_draft(self):
#         for rec in self:
#             rec.state = 'draft'
# #         return self.write({'state': 'draft'})
#
#     @api.model
#     def reducing_balance_method(self, p, r, n):
#         # Determine the interest rate on the loan, the length of the loan and the amount of the loan
#         res = {}
#         for i in range(0, n):
#             step_1_p = p  # principal amount at the beginning of each period
#             step_2_r_m = r / (12 * 100.00)  # interest rate per month
#             step_3_r_m = 1 + step_2_r_m  # add 1 to interest rate per month
#             step_4 = step_3_r_m ** (n - i)  # Raise the step_2_r_m to the power of the number of payments required on the loan
#             step_5 = step_4 - 1  #  minus 1 from step_4
#             step_6 = step_2_r_m / step_5  # Divide the interest rate per month(step_2_r_m) by the step_5
#             step_7 = step_6 + step_2_r_m  # Add the interest rate per month to the step_6
#             step_8_EMI = round(step_7 * step_1_p, 2)  # Total EMI to pay month
#             step_9_int_comp = round(step_1_p * step_2_r_m, 2)  # Total Interest component in EMI
#             step_10_p_comp = round(step_8_EMI - step_9_int_comp, 2)  # Total principal component in EMI
#             p -= step_10_p_comp  # new principal amount
#             res[i] = {'emi':step_8_EMI,
#                       'principal_comp':step_10_p_comp,
#                       'interest_comp':step_9_int_comp
#                       }
#         return res
#
#     @api.model
#     def flat_rate_method(self, principal, rate, duration):
#         return ((principal * rate) / 100)
#
#     @api.multi
#     def create_installments(self):
#         if not self.ids:
#             return True
#         if not self.purchase_date:
#             raise Warning(_('Please give disbursement date.'))
#         else:
#             date_disb = self.purchase_date
#         date_approved_obj = datetime.strptime(date_disb, DEFAULT_SERVER_DATE_FORMAT)
#         installment_obj = self.env['loan.installment.details']
#         ins_ids = installment_obj.search([('test_prepayment_id', '=', self.id)])
#         if ins_ids:
#             ins_ids.unlink()
#         day = date_approved_obj.day
#         month = date_approved_obj.month
#         year = date_approved_obj.year
#         interest_amt = 0.0
#         principal_amt = self.purchase_value / (self.duration or 1.0)
#         if self.int_payable:
#             total_interest_amount = 0.0
#             if self.interest_mode == 'flat':
#                 total_interest_amount = self.flat_rate_method(self.purchase_value, self.int_rate, self.duration)
#             else:
#                 values = self.reducing_balance_method(self.purchase_value, self.int_rate, self.duration)
#                 for key, value in values.iteritems():
#                     total_interest_amount += value['interest_comp']
#             interest_amt = total_interest_amount / (self.duration or 1.0)
#         total = principal_amt + interest_amt
#         if self.interest_mode == 'reducing':
#             reducing_val = self.reducing_balance_method(self.purchase_value, self.int_rate, self.duration)
#
#         old_emi = 0.0
#         old_paid = 0.0
#         for install_no in range(0, self.duration):
#             date_from = datetime(year, month, day)
#             date_to = date_from + relativedelta(months=1)
#             if self.interest_mode == 'reducing':
#                 principal_amt = reducing_val[install_no]['principal_comp']
#                 if self.int_payable:
#                     interest_amt = reducing_val[install_no]['interest_comp']
#                 total = principal_amt + interest_amt
#             day, month, year = date_to.day, date_to.month, date_to.year
#             x = old_emi + old_paid
#             installment_line = {
#                  'install_no':install_no + 1,
#                  'date_from':date_from.strftime('%Y-%m-%d'),
#                  'date_to':date_to.strftime('%Y-%m-%d'),
#                  'principal_amt':principal_amt,
#                  'interest_amt':interest_amt,
#                  'amount_already_paid': x,
#                  'total':total,
#                  'test_prepayment_id':self.id
#              }
#             installment_obj.create(installment_line)
#             old_emi = total
#             old_paid = x
#         return True
#
#     @api.multi
#     def onchange_company_id(self, company_id=False):
#         val = {}
#         if company_id:
#             company = self.env['res.company'].browse(company_id)
# #             if company.currency_id.company_id and company.currency_id.company_id.id != company_id:
# #                 val['currency_id'] = False
# #             else:
# #                 val['currency_id'] = company.currency_id.id#todoprobuse
#         val['currency_id'] = company.currency_id.id
#         return {'value': val}
#
#     @api.one
#     @api.depends('purchase_value', 'total_am')
#     def _amount_residual(self):
#         for s in self:
#             s.value_residual = s.purchase_value - s.total_am
#
#     @api.one
#     @api.depends('depreciation_line_ids')
#     def _total_am(self):
#         for s in self:
#             t = 0.0
#             for k in s.depreciation_line_ids:
#                 if k.state == 'paid':
#                     t += k.total
#             s._total_am = t
#
#     @api.one
#     @api.depends('loan_type')
#     def _get_loan_values(self):
#         for rec in self:
#             allowed_employees = []
#             for categ in rec.loan_type.employee_categ_ids:
#                 allowed_employees += map(lambda x:x.id, categ.employee_ids)
#             allowed_employees += map(lambda x:x.id, rec.loan_type.employee_ids)
#             if rec.emp_id.id in allowed_employees:
#                 rec.int_rate = rec.loan_type.int_rate
#                 rec.interest_mode = rec.loan_type.interest_mode
#                 rec.int_payable = rec.loan_type.int_payable
#
#     value_residual = fields.Float(
#         compute='_amount_residual',
#         digits=dp.get_precision('Account'),
#         string='Closing Balance'
#     )
#     total_am = fields.Float(
#         compute='_total_am',
#         digits=dp.get_precision('Account'),
#         string='Total Deducted'
#     )
#     name = fields.Char(
#         'Name',
#         required=True,
#         readonly=True,
#         states={'draft':[('readonly', False)]},
#         default='/'
#     )
#     method_prepayment = fields.Selection(
#         selection=[('add', 'Additions to existing loan'),
#         ('new', 'Transfer an existing loan')],
#         string='Method',
#         required=True,
#         readonly=True,
#         states={'draft':[('readonly', False)]},
#         help="Choose the method to use for booking loans.\n" \
#             " * Additions to existing loan: Add items to existing loan. \n"\
#             " * Transfer an existing loan: select for an already existing loan. Journals must be raised in GL to book the transfer.",
#         default='new'
#     )
#     emp_id = fields.Many2one(
#         'hr.employee',
#         string='Employee',
#         required=True,
#         readonly=True,
#         states={'draft':[('readonly', False)]}
#     )
#     loan_type = fields.Many2one(
#         'loan.type',
#         string='Loan Type',
#         required=True,
#         readonly=True,
#         states={'draft':[('readonly', False)]}
#     )
#     user_id = fields.Many2one(
#         'res.users',
#         string='Responsible User',
#         required=False,
#         readonly=True,
#         states={'draft':[('readonly', False)]},
#         default=lambda self: self.env.user
#     )
#     state = fields.Selection(
#         selection=[('draft', 'Draft'),
#         ('open1', 'Confirmed'),
#         ('approve', 'Approved'),
#         ('open', 'Running'),
#         ('close', 'Closed'),
#         ('reject', 'Rejected'),
#         ('cancel', 'Cancelled')],
#         string='State',
#         required=True,
#         readonly=True,
#         default='draft'
#     )
#     journal_id1 = fields.Many2one(
#         'account.journal',
#         string='Repayment Board Journal',
#         required=False,
#         readonly=False,
#         states={'close':[('readonly', True)]}
#     )
#     book_gl = fields.Boolean(
#         string='Book Transfer to GL?',
#         states={'draft':[('readonly', False)]},
#         readonly=True
#     )
#     gl_account_id = fields.Many2one(
#         'account.account',
#         string='GL Account',
#         readonly=True,
#         required=False,
#         states={'draft':[('readonly', False)]}
#     )
#     move_id1 = fields.Many2one(
#         'account.move',
#         string='Journal Entry 1',
#         readonly=True
#     )
#     journal_id = fields.Many2one(
#         'account.journal',
#         string='Book Transfer Journal',
#         required=False
#     )
#     code = fields.Char(
#         string='Reference',
#         readonly=True,
#         states={'draft':[('readonly', False)]}
#     )
#     purchase_value = fields.Float(
#         string='Transferred Balance ',
#         required=True,
#         readonly=True,
#         states={'draft':[('readonly', False)]}
#     )
#     currency_id = fields.Many2one(
#         'res.currency',
#         string='Currency',
#         required=True,
#         readonly=True,
#         states={'draft':[('readonly', False)]},
#         default=lambda self: self.env.user.company_id.currency_id
#     )
#     company_id = fields.Many2one(
#         'res.company',
#         string='Company',
#         required=True,
#         readonly=True, states={'draft':[('readonly', False)]},
#         default=lambda self:self.env['res.company']._company_default_get('loan.prepayment')
#     )
#     note = fields.Text(string='Note')
#     purchase_date = fields.Date(string='Transfer Date', required=True, readonly=True, help='This date will be the date when the Loan will be considered to be Starting in the system and this date will be used in the Repayment Board.', states={'draft':[('readonly', False)]}, default=time.strftime('%Y-%m-%d'))
#     active = fields.Boolean(string='Active', default=True)
#     employee_loan_account = fields.Many2one('account.account', string="Employee Account", readonly=False, states={'disburse':[('readonly', True)]})
#     duration = fields.Integer(string='Duration(Months)', required=True, readonly=True, states={'draft':[('readonly', False)]})
#     depreciation_line_ids = fields.One2many('loan.installment.details', 'test_prepayment_id', string='Repayments Lines', readonly=True, states={'draft':[('readonly', False)], 'open':[('readonly', False)]})
#     original_amount = fields.Float(string='Original Amount', digits=dp.get_precision('Account'), readonly=True, states={'draft':[('readonly', False)]})
#     account_analytic_id = fields.Many2one('account.analytic.account', string='Analytic Account', states={'draft':[('readonly', False)]}, readonly=True)
#
#     int_payable = fields.Boolean(compute='_get_loan_values', string='Is Interest Payable', store=True)
#     int_rate = fields.Float(compute='_get_loan_values', string='Rate(Per Annum)', help='Interest rate between 0-100 in range', digits=(16, 2), store=True)
#     interest_mode = fields.Selection(compute='_get_loan_values', selection=[('flat', 'Flat'), ('reducing', 'Reducing'), ('', '')], string='Interest Mode', store=True)
#
#     @api.multi
#     def approve(self):
# #         period_obj = self.env['account.period']
#         move_obj = self.env['account.move']
#         move_line_obj = self.env['account.move.line']
#         currency_obj = self.env['res.currency']
#         for a in self:
#             if a.book_gl:
# #                 period_ids = period_obj.find(a.purchase_date)
#                 move_vals = {
#                     'date': a.purchase_date,
#                     'ref': a.name,
# #                     'period_id': period_ids and period_ids.id or False,
#                     'journal_id': a.journal_id.id,
#                 }
#                 move_id = move_obj.create(move_vals)
#                 journal_id = a.journal_id.id
#                 partner_id = a.emp_id.address_home_id.id
#                 company_currency = a.company_id.currency_id
#                 current_currency = a.currency_id
#                 ctx = dict(self._context or {})
#                 ctx.update({'date': time.strftime('%Y-%m-%d')})
#                 amount = current_currency.compute(a.purchase_value, company_currency)
#
#                 sign = a.journal_id.type == 'purchase' and 1 or -1
#                 if not a.employee_loan_account:
#                     raise Warning( _('Please configure loan account of employee'))
#
#                 move_line_obj.create({
#                     'name': a.name,
#                     'ref': a.name,
#                     'move_id': move_id.id,
#                     'account_id': a.gl_account_id.id,
#                     'debit': 0.0,
#                     'credit': amount,
# #                     'period_id': period_ids and period_ids.id or False,
#                     'journal_id': journal_id,
#                     'partner_id': partner_id,
#                     'currency_id': company_currency.id <> current_currency.id and current_currency.id or False,
#                     'amount_currency': company_currency.id <> current_currency.id and -sign * a.purchase_value or 0.0,
#                     'date': a.purchase_date,
#                 })
#                 move_line_obj.create({
#                     'name': a.name,
#                     'ref': a.name,
#                     'move_id': move_id.id,
#                     'account_id': a.employee_loan_account.id,
#                     'analytic_account_id': a.account_analytic_id.id or False,
#                     'credit': 0.0,
#                     'debit': amount,
# #                     'period_id': period_ids and period_ids.ids or False,
#                     'journal_id': journal_id,
#                     'partner_id': partner_id,
#                     'currency_id': company_currency.id <> current_currency.id and current_currency.id or False,
#                     'amount_currency': company_currency.id <> current_currency.id and sign * a.purchase_value or 0.0,
#                     'date': a.purchase_date,
#                 })
#                 a.write({'move_id1': move_id.id})
#                 a.state = 'approve'
# #         return self.write({'state': 'approve'})
#
#     @api.multi
#     def set_to_cancel(self):
#         for rec in self:
#             rec.state = 'cancel'

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
