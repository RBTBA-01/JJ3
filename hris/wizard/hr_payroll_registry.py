# -*- coding:utf-8 -*-

from odoo import fields, models, api
from odoo.tools import DEFAULT_SERVER_DATE_FORMAT
import xlwt
import base64
import cStringIO
from datetime import datetime

class HRPayrollRegistry(models.TransientModel):
    _name = 'hr.payroll.registry'
    _description = 'HR Payroll Registry Report'


    @api.multi
    def action_generate_xls_report(self):
        self.ensure_one()
        work_book = xlwt.Workbook(encoding='utf-8')
        
        #xlwt.add_palette_colour("custom_colour", 0x21)
        work_book.set_colour_RGB(0x21, 243, 20, 28)
        # company = self.env['res.company']._company_default_get('hris')
        company = self.company_id

        header_content_style = xlwt.easyxf("font: name Helvetica size 25 px, bold 1, height 170;")
        sub_header_style = xlwt.easyxf("font: name Helvetica size 10 px, bold 1, height 170;")
        header_style = xlwt.easyxf("font: name Helvetica size 10 px, bold 1, height 170;pattern: pattern solid, fore_colour yellow;")
        sub_header_content_style = xlwt.easyxf("font: name Helvetica size 10 px, height 170;")
        row_style = xlwt.easyxf("font: name Helvetica, height 170;")
        total_row_style = xlwt.easyxf("font: name Helvetica, height 170;align: horiz right")
        
        total_style = xlwt.easyxf("font: name Helvetica size 10 px, bold 1, height 170;pattern: pattern solid, fore_colour yellow;align: horiz right")
        
        for report_config in self.env['payroll.register.config'].search([('id', 'in', self.config_ids.ids)]):
            
            work_sheet = work_book.add_sheet(report_config.name)
            fp = cStringIO.StringIO()
            
            row = 1
            col = 0
            work_sheet.write_merge(row, row, 2, 6, report_config.name, header_content_style)
            row += 2
            work_sheet.write(row, col + 0, company.name, sub_header_style)
            row += 2
            work_sheet.write(row, col + 0, "Date Release From:", sub_header_style)
            work_sheet.write(row, col + 1, datetime.strftime(datetime.strptime(self.date_release_from, DEFAULT_SERVER_DATE_FORMAT), "%d/%m/%Y"),sub_header_content_style)
            work_sheet.write(row, col + 2, "Date Release To:", sub_header_style)
            if self.date_release_to:
                work_sheet.write(row, col + 3, datetime.strftime(datetime.strptime(self.date_release_to, DEFAULT_SERVER_DATE_FORMAT), "%d/%m/%Y"),sub_header_content_style)
            else:
                work_sheet.write(row, col + 3, " ",sub_header_content_style)
            row += 2
           
            work_sheet.write(row, col + 1, "Employee No.", header_style)
            work_sheet.write(row, col + 2, "Employee Name", header_style)
            work_sheet.write(row, col + 3, "Bank Account", header_style)
            work_sheet.write(row, col + 4, "Position", header_style)
            col_counter = 5
            
            for config in self.env['payroll.register.config'].search([('id', '=', report_config.id)]):
                        
                for line in config.config_line.sorted(lambda r:r.sequence):
                    work_sheet.write(row, col + col_counter, line.name, header_style)
                    col_counter += 1
            
            row += 1
            employees = self.employee_ids
            date_from = self.payroll_period_from_id.start_date
            date_to = self.payroll_period_to_id and self.payroll_period_to_id.end_date or self.payroll_period_from_id.end_date
            domain = [('employee_id','in', employees.ids),('credit_note','=', False),('date_from', '>=', date_from),
                      ('date_to','<=', date_to), ('state', 'in', ['draft', 'done'])]
            payslips = self.env['hr.payslip'].search(domain)
            total_amount = {}
            for employee in employees.sorted(key=lambda r:r.name):
                if employee:
                    
#                     domain = [('employee_id', '=', employee.id),('payroll_period_id', '=', self.payroll_period_from_id.id)]
#                     domain = [('employee_id', '=', employee.id),('date_from', '>=', self.date_from), ('date_to', '<=', self.date_to)]
#                     payslips = self.env['hr.payslip'].search(domain, order="number desc", limit=2)
                    # bank_account = employee.bank_account_id and employee.bank_account_id.acc_number or ''
                    bank_account = employee.bank_id and employee.bank_account_no or ''
                        
                    work_sheet.write(row, 1, employee.barcode or '', row_style)
                    work_sheet.write(row, 2, employee.name, row_style)
                    work_sheet.write(row, 3, bank_account, row_style)
                    work_sheet.write(row, 4, employee.job_id.name or '', row_style)
                    list = []
                    for config in self.env['payroll.register.config'].search([('id', '=', report_config.id)]):
                        payslip_ids = payslips.filtered(lambda p: p.employee_id == employee)
                        col_counter = 5
                        if payslip_ids:
                            for line in config.config_line.sorted(lambda r:r.sequence):
                                
                                rule_ids = {
                                    (line.sequence, line.from_salary_computation,line.from_worked_days): 
                                    line.salary_rule_ids.mapped('code')
                                    }
                                
                                key,values = rule_ids.items()[0]
                                seq, from_salary_comp, from_worked_day = key
                                amount = 0
                                if from_worked_day:
                                    amount = sum(sum(payslip_ids.mapped('worked_days_line_ids').filtered(lambda l: l.code == code).mapped('number_of_hours')) for code in set(values))
                                else:
                                    amount = sum(sum(payslip_ids.mapped('line_ids').filtered(lambda l: l.salary_rule_id.code == code or l.code == code).mapped('amount')) for code in set(values))
                                work_sheet.write(row, col_counter, "{:,.2f}".format(amount), total_row_style)
                                
                                col_counter += 1
                                
                                if seq in total_amount:
                                    total_amount[seq] = total_amount.get(seq, 0) + amount
                                else:
                                    total_amount[seq] = amount
                    row += 1           
                        
            row += 1
            work_sheet.write_merge(row, row, 0, 4, "TOTAL", header_style)
            
            col = 5
            for x,totals in total_amount.items():
                work_sheet.write(row, col, "{:,.2f}".format(totals), total_style)
                col += 1
            
            work_book.save(fp)
        
        out = base64.encodestring(fp.getvalue())
        self.write({'state': 'done', 'report': out, 'name': 'payroll_registry_details.xls'})
        return {
            'type': 'ir.actions.act_window',
            'name': 'Payroll Registry',
            'res_model': 'hr.payroll.registry',
            'view_mode': 'form',
            'view_type': 'form',
            'res_id': self.id,
            'views': [(False, 'form')],
            'target': 'new',
        }

    @api.depends('payroll_period_from_id','payroll_period_to_id', 'company_id')
    def get_employees(self):
        for rec in self:
            date_from = rec.payroll_period_from_id.start_date
            date_to = rec.payroll_period_to_id and rec.payroll_period_to_id.end_date or rec.payroll_period_from_id.end_date
            payslips = self.env['hr.payslip'].search([('date_from', '>=', date_from),
                                                      ('date_to','<=', date_to),
                                                      ('state', 'in', ['draft', 'done']),
                                                      ('credit_note','=', False),
                                                      ('id_company', '=', rec.company_id.id)
                                                    ])
            rec.employee_ids = [(6, 0, payslips.mapped('employee_id.id'))]

    @api.onchange('payroll_period_id')
    def onchange_payroll_period(self):
        if self.payroll_period_id:
            self.date_release = self.payroll_period_id.date_release
    
    def _get_default_start_date(self):
        year = fields.Date.from_string(fields.Date.today()).replace(month=1,day=1).strftime('%Y-%m-%d')
        return year

    def _get_default_end_date(self):
        _date = fields.Date.context_today(self)
        return _date

    date_release_from = fields.Date('Date Release', required=True)
    date_release_to = fields.Date('Date Release')
    payroll_period_from_id = fields.Many2one('hr.payroll.period_line', 'Payroll Period From', required=True)
    payroll_period_to_id = fields.Many2one('hr.payroll.period_line', 'Payroll Period To')
    config_ids = fields.Many2many('payroll.register.config', 'payroll_register_config_report_rel', 'config_id', 'payroll_register_config_id', 'Reports to Generate', required=True)
    date_payroll = fields.Date(string='Payroll Date', required=True, default=_get_default_end_date)
    date_from = fields.Date(string='Start Date', required=True, default=_get_default_start_date)
    date_to = fields.Date(string='End Date', required=True, default=_get_default_end_date)
    employee_ids = fields.Many2many('hr.employee', 'payroll_registry_rel', 'payroll_registry_id', 'employee_id', string='Employees', required=True,
                                    compute='get_employees',readonly=False, store=True)
    company_id = fields.Many2one('res.company', string="Company", required=True)
    report = fields.Binary('Prepared file', filters='.xls', readonly=True)
    name = fields.Char('File Name', size=32)
    state = fields.Selection([('draft', 'Draft'), ('done', ' Done')], 'State', default='draft')

    @api.onchange('payroll_period_from_id','payroll_period_from_id')
    def onchange_payroll_period(self):
        if self.payroll_period_from_id:
            self.date_from = self.payroll_period_from_id.start_date
            self.date_to = self.payroll_period_from_id.end_date
            self.date_release_from = self.payroll_period_from_id.date_release
        if self.payroll_period_from_id and self.payroll_period_to_id:
            self.date_from = self.payroll_period_from_id.start_date
            self.date_to = self.payroll_period_to_id.end_date
            self.date_release_to = self.payroll_period_to_id.date_release

    @api.multi
    def print_report(self):
        """
         To get the date and print the report
         @return: return report
        """
        self.ensure_one()
        data = {'ids': self.env.context.get('active_ids', [])}
        res = self.read()
        res = res and res[0] or {}
        data.update({'form': res})

        return self.env['report'].get_action(self, 'hris.report_hrpayrollregistry_template', data=data)