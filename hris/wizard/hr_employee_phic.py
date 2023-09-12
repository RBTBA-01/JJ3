# -*- coding:utf-8 -*-
from odoo import fields, models, api
from odoo.tools import DEFAULT_SERVER_DATE_FORMAT
import xlwt
import base64
import cStringIO

from datetime import datetime

class HRPHIC(models.TransientModel):
    _name = 'hr.payroll.phic_report'

    @api.multi
    def action_generate_xls_report(self):
        self.ensure_one()
        wb1 = xlwt.Workbook(encoding='utf-8')
        ws1 = wb1.add_sheet('PHIC REPORT')
        fp = cStringIO.StringIO()
        xlwt.add_palette_colour("custom_colour", 0x21)
        wb1.set_colour_RGB(0x21, 243, 20, 28)
        company = self.env['res.company']._company_default_get('hris')

        header_content_style = xlwt.easyxf("font: name Helvetica size 25 px, bold 1, height 170;")
        sub_header_style = xlwt.easyxf("font: name Helvetica size 10 px, bold 1, height 170;")
        header_style = xlwt.easyxf("font: name Helvetica size 10 px, bold 1, height 170;pattern: pattern solid, fore_colour yellow;")
        sub_header_content_style = xlwt.easyxf("font: name Helvetica size 10 px, height 170;")
        row_style = xlwt.easyxf("font: name Helvetica, height 170;")

        total_row_style = xlwt.easyxf("font: name Helvetica, height 170;align: horiz right")
        total_style = xlwt.easyxf("font: name Helvetica size 10 px, bold 1, height 170;pattern: pattern solid, fore_colour yellow;align: horiz right")

        row = 1
        col = 0
        ws1.write_merge(row, row, 3, 6, "PHIC REPORT", header_content_style)
        row += 2
        ws1.write(row, col + 0, company.name, sub_header_style)
        ws1.write(row, col + 3, "Employer's PHIC No. :", sub_header_style)
        ws1.write(row, col + 5 , company.philhealth_num or '', sub_header_style)
        row +=2
        ws1.write(row, col + 0, "From :", sub_header_style)
        ws1.write(row, col + 1, datetime.strftime(datetime.strptime(self.date_from, DEFAULT_SERVER_DATE_FORMAT),"%d/%m/%Y"), sub_header_content_style)
        row += 1
        ws1.write(row, col + 0, "To :", sub_header_style)
        ws1.write(row, col + 1, datetime.strftime(datetime.strptime(self.date_to, DEFAULT_SERVER_DATE_FORMAT),"%d/%m/%Y"), sub_header_content_style)
        row += 2
        ws1.write(row, col + 0, "PHIC No.", header_style)
        ws1.write(row, col + 1, "LAST NAME", header_style)
        ws1.write(row, col + 2, "FIRST NAME", header_style)
        ws1.write(row, col + 3, "MIDDLE NAME", header_style)
        ws1.write(row, col + 4, "EE", header_style)
        ws1.write(row, col + 5, "ER", header_style)
        row += 1

        phic_emp_tot = 0
        phic_er_tot = 0

        for employee in self.employee_ids:
            payslip = self.env['hr.payslip'].search([('employee_id', '=', employee.id), ('credit_note','=',False),('date_from', '>=', self.date_from),
             ('date_from', '<=', self.date_to), ('state', 'in', ['draft', 'done'])])

            line_ee = self.env['hr.payslip.line']
            line_er = self.env['hr.payslip.line']
            for record in payslip:
                line_ee |= record.line_ids.filtered(lambda r:r.code in ('PHIC-M', 'PHIC-SM'))
            phic_ee = sum(line_ee.mapped('total'))
            
            for record2 in payslip:
                line_er |= record2.line_ids.filtered(lambda r:r.code in ('PHICER-M', 'PHICER-SM'))
            phic_er = sum(line_er.mapped('total'))

            ws1.write(row, col + 0, employee.phic_no, row_style)
            ws1.write(row, col + 1, employee.lastname, row_style)
            ws1.write(row, col + 2, employee.firstname, row_style)
            ws1.write(row, col + 3, employee.middlename or '', row_style)
            ws1.write(row, col + 4, '{:,.2f}'.format(phic_ee), total_row_style)
            ws1.write(row, col + 5, '{:,.2f}'.format(phic_er), total_row_style)

            row += 1
            phic_emp_tot += phic_ee
            phic_er_tot += phic_er

        row += 1
        ws1.write_merge(row, row, 0, 3, "TOTAL", total_style)
        ws1.write(row, col + 4, '{:,.2f}'.format(phic_emp_tot), total_style)
        ws1.write(row, col + 5, '{:,.2f}'.format(phic_er_tot), total_style)
        wb1.save(fp)
        out = base64.encodestring(fp.getvalue())
        self.write({'state': 'done', 'report': out, 'name': 'phic_report_details.xls'})
        return {
            'type': 'ir.actions.act_window',
            'name': 'PHIC REPORT',
            'res_model': 'hr.payroll.phic_report',
            'view_mode': 'form',
            'view_type': 'form',
            'res_id': self.id,
            'views': [(False, 'form')],
            'target': 'new',
        }

    def _get_default_start_date(self):
        start_date = fields.Date.from_string(fields.Date.today()).replace(month=1, day=1).strftime('%Y-%m-%d')
        return start_date

    def _get_default_end_date(self):
        date = fields.Date.from_string(fields.Date.today())
        return date.strftime('%Y-%m-%d')

    date_from = fields.Date(string='Start Date', required=True, default=_get_default_start_date)
    date_to = fields.Date(string='End Date', required=True, default=_get_default_end_date)
    date_now = fields.Date(string='date_now', required=True, default= fields.Date.today())
    employee_ids = fields.Many2many('hr.employee', string='Employees', required=True)
    report = fields.Binary('Prepared file', filters='.xls', readonly=True)
    name = fields.Char('File Name', size=32)
    state = fields.Selection([('draft', 'Draft'), ('done', ' Done')], 'State', default='draft')

    @api.multi
    def print_report(self):

        self.ensure_one()
        data = {'ids': self.env.context.get('active_ids', [])}
        res = self.read()
        res = res and res[0] or {}
        data.update({'form': res})

        # id from hr_employee_bir_report.xml
        return self.env['report'].get_action(self, 'hris.report_phic', data=data)
