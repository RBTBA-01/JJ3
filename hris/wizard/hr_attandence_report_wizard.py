# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
import base64
import io
import datetime
from odoo import models, fields, api,_
from odoo import tools

from datetime import date
from odoo.fields import Datetime
from odoo.exceptions import ValidationError
from xlsxwriter import Workbook
from odoo.tools.misc import xlsxwriter
# from xlsxwriter.utility import xl_rowcol_to_cell

context_timestamp = Datetime.context_timestamp


class EntrivisDailySaleWizard(models.TransientModel):
    _name = 'attendance.report.wizard'
    _description = 'Attendance Report Wizard'

    excel_file = fields.Binary('Report file ')
    file_name = fields.Char('Excel File', size=64)
    start_date = fields.Date("Start Date")
    end_date = fields.Date("End Date",default=date.today())
    employee_ids = fields.Many2many('hr.employee', string='Employees')
    state = fields.Selection([('draft', 'Draft'), ('done', ' Done')], string='State', default='draft')

    @api.constrains('start_date','end_date')
    def date_constrains(self):
        for rec in self:
            current_date = str(date.today())
            if rec.start_date > rec.end_date:
                raise ValidationError(_('Sorry, Start Date is not be greater than End Date...'))
            elif rec.start_date > rec.end_date:
                raise ValidationError(_('Sorry ,End Date Should Not be Future Date'))

    def generate_report(self):
        output = io.BytesIO()
        file_name = ('Daily Attendance Report')
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        header_style = workbook.add_format({'bold': True,
                                            'font_size' : 11,
                                            'align': 'center',
                                            'valign': 'vcenter',
                                            'border': 1})
        body_style = workbook.add_format({'border': 1,
                                          'align': 'center',
                                          'valign': 'vcenter',
                                          'font_size' : 9,
                                          })
        worksheet = workbook.add_worksheet('Employee Attendance Report')

        domain = [('schedule_in', '>=', self.start_date),('schedule_in', '<=', self.end_date)]
        if self.employee_ids:
             domain.append(('employee_id', 'in' , self.employee_ids.ids))
        
        attendance_line = self.env['hr.attendance'].search(domain)


        worksheet.set_column(0 ,0,7)
        worksheet.set_column(1,5,16)
        worksheet.set_column(6,13,12)
        worksheet.set_column(14,25,17)

        
        row=0
        col=0
        worksheet.set_row(row, 25)
        worksheet.freeze_panes(1,0)
        worksheet.write(row, col, 'S.No', header_style)
        col += 1
        worksheet.write(row, col, 'Employee', header_style)
        col += 1
        worksheet.write(row, col, 'Schedule In', header_style)
        col += 1
        worksheet.write(row, col, 'Schedule Out', header_style)
        col += 1
        worksheet.write(row, col, 'Actual Time In', header_style)
        col += 1
        worksheet.write(row, col, 'Actual Time Out', header_style)
        col += 1
        worksheet.write(row, col, 'Regular Hour', header_style)
        col += 1
        worksheet.write(row, col, 'Absent Hour', header_style)
        col += 1
        worksheet.write(row, col, 'OB Hour', header_style)
        col += 1
        worksheet.write(row, col, 'LWP Hour', header_style)
        col += 1
        worksheet.write(row, col, 'LWOP Hour', header_style)
        col += 1
        worksheet.write(row, col, 'Late', header_style)
        col += 1
        worksheet.write(row, col, 'Undertime', header_style)
        col += 1
        worksheet.write(row, col, 'Rest Day', header_style)
        col += 1
        worksheet.write(row, col, 'Special Holiday', header_style)
        col += 1
        worksheet.write(row, col, 'Regular Holiday', header_style)
        col += 1
        worksheet.write(row, col, 'Night Shift Differential', header_style)
        col += 1
        worksheet.write(row, col, 'Regular Overtime', header_style)
        col += 1
        worksheet.write(row, col, 'Rest Day Overtime', header_style)
        col += 1
        worksheet.write(row, col, 'Special Holiday Overtime', header_style)
        col += 1
        worksheet.write(row, col, 'Regular Holiday Overtime', header_style)
        col += 1
        worksheet.write(row, col, 'Night Differential Overtime', header_style)
        col += 1
        worksheet.write(row, col, 'Check-in Remarks', header_style)
        col += 1
        worksheet.write(row, col, 'Check-out Remarks', header_style)
        col += 1
        worksheet.write(row, col, 'Remarks', header_style)
        row = +1
        col = 0
        no = 1
        for res in attendance_line:
            schedule_in = context_timestamp(self, fields.Datetime.from_string(res.schedule_in))
            schedule_out = context_timestamp(self, fields.Datetime.from_string(res.schedule_out))
            if res.check_in or res.check_out:
                check_in = res.check_in and context_timestamp(self, fields.Datetime.from_string(res.check_in)) or ''
                check_out = res.check_out and context_timestamp(self, fields.Datetime.from_string(res.check_out)) or ''
                is_hide_check_time = res.is_hide_check_time
            else:
                is_hide_check_time = True
            col = 0
            worksheet.write(row, col, no, body_style ,)
            col += 1
            worksheet.write(row, col ,res.employee_id.name , body_style)
            col += 1
            worksheet.write(row, col , schedule_in.strftime("%Y-%m-%d %H:%M:%S") , body_style)
            col += 1
            worksheet.write(row, col ,schedule_out.strftime("%Y-%m-%d %H:%M:%S")  , body_style)
            col += 1
            if is_hide_check_time:
                col +=2
            else:
                worksheet.write(row, col ,check_in.strftime("%Y-%m-%d %H:%M:%S"), body_style)
                col += 1
                worksheet.write(row, col ,check_out.strftime("%Y-%m-%d %H:%M:%S"), body_style)
                col += 1
            worksheet.write(row, col ,res.worked_hours , body_style)
            col += 1
            worksheet.write(row, col ,res.absent_hours , body_style)
            col += 1
            worksheet.write(row, col ,res.ob_hours, body_style)
            col += 1
            worksheet.write(row, col ,res.leave_hours, body_style)
            col += 1
            worksheet.write(row, col ,res.leave_wop_hours, body_style)
            col += 1
            worksheet.write(row, col ,res.late_hours, body_style)
            col += 1
            worksheet.write(row, col ,res.undertime_hours, body_style)
            col += 1
            worksheet.write(row, col ,res.rest_day_hours, body_style)
            col += 1
            worksheet.write(row, col ,res.sp_holiday_hours , body_style)
            col += 1
            worksheet.write(row, col ,res.reg_holiday_hours , body_style)
            col += 1
            worksheet.write(row, col ,res.night_diff_hours , body_style)
            col += 1
            worksheet.write(row, col ,res.overtime_hours, body_style)
            col += 1
            worksheet.write(row, col ,res.rest_day_ot_hours, body_style)
            col += 1
            worksheet.write(row, col ,res.sp_hday_ot_hours, body_style)
            col += 1
            worksheet.write(row, col ,res.reg_hday_ot_hours, body_style)
            col += 1
            worksheet.write(row, col ,res.night_diff_ot_hours , body_style)
            col += 1
            worksheet.write(row, col ,res.checkin_remarks , body_style)
            col += 1
            worksheet.write(row, col ,res.checkout_remarks , body_style)
            col += 1
            worksheet.write(row, col ,res.remarks, body_style)
            row += 1 
            no += 1


        workbook.close()
        output.seek(0)
        report_file = base64.encodestring(output.getvalue())
        output.close()
        self.write({'state': 'done', 'excel_file':report_file , 'file_name':file_name})

        return {
            'view_mode': 'form',
            'res_id': self.id,
            'name': 'Attendance Report',
            'res_model': 'attendance.report.wizard',
            'view_type': 'form',
            'type': 'ir.actions.act_window',
            'target':'new'
        }
