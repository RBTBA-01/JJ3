# -*- coding:utf-8 -*-
from odoo import fields, models, api
from odoo.tools import DEFAULT_SERVER_DATE_FORMAT
import xlwt
import base64
import cStringIO
from datetime import datetime

class HRHDMF(models.TransientModel):
    _name = 'hr.hdmf.contribution'
    _description = 'HR HDMF contribution report'

    @api.multi
    def action_generate_xls_report(self):
        self.ensure_one()
        wb1 = xlwt.Workbook(encoding='utf-8')
        ws1 = wb1.add_sheet('HDMF REPORT')
        fp = cStringIO.StringIO()
        xlwt.add_palette_colour("custom_colour", 0x21)
        wb1.set_colour_RGB(0x21, 243, 20, 28)
        company = self.env['res.company']._company_default_get('hris')

        header_content_style = xlwt.easyxf("font: name Helvetica size 25 px, bold 1, height 170;")
        sub_header_style = xlwt.easyxf("font: name Helvetica size 10 px, bold 1, height 170;")
        header_style = xlwt.easyxf(
            "font: name Helvetica size 10 px, bold 1, height 170;pattern: pattern solid, fore_colour yellow;")
        sub_header_content_style = xlwt.easyxf("font: name Helvetica size 10 px, height 170;")
        row_style = xlwt.easyxf("font: name Helvetica, height 170;")
        
        total_row_style = xlwt.easyxf("font: name Helvetica, height 170;align: horiz right")
        total_style = xlwt.easyxf("font: name Helvetica size 10 px, bold 1, height 170;pattern: pattern solid, fore_colour yellow;align: horiz right")
        
        row = 1
        col = 0
        ws1.write_merge(row, row, 3, 6, "HDMF REPORT", header_content_style)
        row += 2
        ws1.write(row, col + 0, company.name, sub_header_style)
        ws1.write(row, col + 3, "Employer's HDMF No. :", sub_header_style)
        ws1.write(row, col + 5, company.pagibig_num or '', sub_header_style)
        row += 2
        ws1.write(row, col+0, "From :", sub_header_style)
        ws1.write(row, col+1, datetime.strftime(datetime.strptime(self.date_from,DEFAULT_SERVER_DATE_FORMAT),"%d/%m/%Y"), sub_header_content_style)
        row += 1
        ws1.write(row, col+0, "To :", sub_header_style)
        ws1.write(row, col+1, datetime.strftime(datetime.strptime(self.date_to,DEFAULT_SERVER_DATE_FORMAT),"%d/%m/%Y"), sub_header_content_style)
        row += 2
        ws1.write(row, col + 0, "HDMF No.", header_style)
        ws1.write(row, col + 1, "TIN No.", header_style)
        ws1.write(row, col + 2, "LAST NAME", header_style)
        ws1.write(row, col + 3, "FIRST NAME", header_style)
        ws1.write(row, col + 4, "MIDDLE NAME", header_style)
        ws1.write(row, col + 5, "BIRTH DATE", header_style)
        ws1.write(row, col + 6, "EE", header_style)
        ws1.write(row, col + 7, "ER", header_style)
        row += 1
        
        hdmf_tot_ee = 0
        hdmf_tot_er = 0

        for employee in self.employee_ids:
            payslip = self.env['hr.payslip'].search([('employee_id', '=', employee.id), ('credit_note','=',False), ('date_release', '>=', self.date_from),
             ('date_release', '<=', self.date_to), ('state', 'in', ['draft', 'done'])])
            
            line_ee = self.env['hr.payslip.line']
            line_er = self.env['hr.payslip.line']
            for record in payslip:
                
                line_ee |= record.line_ids.filtered(lambda r:r.code in ('HDMF-M', 'HDMF-SM'))
            
            hdmf_ee = sum(line_ee.mapped('total'))
            
            for record2 in payslip:
                line_er |= record2.line_ids.filtered(lambda r:r.code in ('HDMFER-M', 'HDMFER-SM'))
            
            hdmf_er = sum(line_er.mapped('total'))
            
            ws1.write(row, col + 0, employee.hdmf_no or '', row_style)
            ws1.write(row, col + 1, employee.identification_id or '', row_style)
            ws1.write(row, col + 2, employee.lastname, row_style)
            ws1.write(row, col + 3, employee.firstname, row_style)
            ws1.write(row, col + 4, employee.middlename or '', row_style)
            ws1.write(row, col + 5, employee.birthday or '', row_style)
            ws1.write(row, col + 6, "{:,.2f}".format(hdmf_ee), total_row_style)
            ws1.write(row, col + 7, "{:,.2f}".format(hdmf_er), total_row_style)
            row += 1
            
            hdmf_tot_ee += hdmf_ee
            hdmf_tot_er += hdmf_er
            
        row += 1
        ws1.write_merge(row, row, 0, 5, "TOTAL", total_style)
        ws1.write(row, col + 6, "{:,.2f}".format(hdmf_tot_ee), total_style)
        ws1.write(row, col + 7, "{:,.2f}".format(hdmf_tot_er), total_style)
        wb1.save(fp)
        
        out = base64.encodestring(fp.getvalue())
        self.write({'state': 'done', 'report': out, 'name': 'hdmf_report_details.xls'})
        return {
            'type': 'ir.actions.act_window',
            'name': 'HDMF REPORT',
            'res_model': 'hr.hdmf.contribution',
            'view_mode': 'form',
            'view_type': 'form',
            'res_id': self.id,
            'views': [(False, 'form')],
            'target': 'new',
        }



    def _get_default_start_date(self):
        year = fields.Date.from_string(fields.Date.today()).strftime('%Y')
        return '{}-01-01'.format(year)

    def _get_default_end_date(self):
        date = fields.Date.from_string(fields.Date.today())
        return date.strftime('%Y') + '-' + date.strftime('%m') + '-' + date.strftime('%d')

    date_payroll = fields.Date(string='Payroll Date', required=True, default=_get_default_end_date)
    date_from = fields.Date(string='Start Date', required=True, default=_get_default_start_date)
    date_to = fields.Date(string='End Date', required=True, default=_get_default_end_date)
    employee_ids = fields.Many2many('hr.employee', 'hdmf_contribution_rel', 'hdmf_contribution_id', 'employee_id', string='Employees', required=True)
    report = fields.Binary('Prepared file', filters='.xls', readonly=True)
    name = fields.Char('File Name', size=32)
    state = fields.Selection([('draft', 'Draft'), ('done', ' Done')], 'State', default='draft')

    @api.multi
    def action_generate_text_report(self):
        self.ensure_one()
        fp = open('hdmf_report.txt', 'w')
        company = self.env['res.company']._company_default_get('hris')
        address = str(company.street or '') + " " + str(company.street2 or '') + " " + str(company.city or '') + " " +\
            str(company.state_id and company.state_id.name or '') + " " +\
            str(company.country_id and company.country_id.name or '')
        phone = str(company.phone or company.mobile)
        today = datetime.now().strftime(DEFAULT_SERVER_DATE_FORMAT)
        date = "EH" + datetime.strftime(datetime.strptime(today,DEFAULT_SERVER_DATE_FORMAT),"%m%Y")

        fp.write("{0:8} {1:20} {2:70} {3:80} {4:7} {5:10}".format(date, company.company_registry, company.name, address, company.zip, phone)+ "\n")
        # company_info = "{0:10} {1:10}".format((company.zip or ''), (company.phone or company.mobile))
        # fp.write("\t\t\t\t\t\t\t\t\t\t\t" + company_info + "\n\n")

        for employee in self.employee_ids:
            payslip = self.env['hr.payslip'].search([('employee_id', '=', employee.id), ('credit_note','=',False), ('date_release', '>=', self.date_from),
             ('date_release', '<=', self.date_to), ('state', '=', 'done')])

            line_ee = self.env['hr.payslip.line']
            line_er = self.env['hr.payslip.line']
            for record in payslip:
                
                line_ee |= record.line_ids.filtered(lambda r:r.code in ('HDMF-M', 'HDMF-SM'))

            hdmf_ee = sum(line_ee.mapped('total'))

            for record2 in payslip:
                line_er |= record2.line_ids.filtered(lambda r:r.code in ('HDMFER-M', 'HDMFER-SM'))

            hdmf_er = sum(line_er.mapped('total'))

            employee_info = "{0:30} {1:40} {2:40} {3:40} {4:13} {5:21} {6:8}".format(employee.hdmf_no or '', employee.lastname or '', employee.firstname or '', employee.middlename or '', "{:,.2f}".format(hdmf_ee), "{:,.2f}".format(hdmf_er), employee.birthday or '')
            fp.write("\n" + employee_info)
        fp.close()

        #Final File
        file = open('hdmf_report.txt', 'r')
        out = file.read()
        file.close()

        # out = base64.b64encode(fp)
        self.write({'state': 'done', 'report': base64.b64encode(out), 'name': 'hdmf_report_details.txt'})
        return {
            'type': 'ir.actions.act_window',
            'name': 'HDMF REPORT',
            'res_model': 'hr.hdmf.contribution',
            'view_mode': 'form',
            'view_type': 'form',
            'res_id': self.id,
            'views': [(False, 'form')],
            'target': 'new',
        }


    @api.multi
    def print_report(self):
        """
         To get the date and print the report
         @return: return report
        """
        company_id = self.env['res.company']._company_default_get('hris')

        self.ensure_one()
        data = {'ids': self.env.context.get('active_ids', []),
                'company_id' : company_id.id
                }
        res = self.read()
        res = res and res[0] or {}
        data.update({'form': res})


        return self.env['report'].get_action(self, 'hris.report_hdmf_contribution_template', data=data)
