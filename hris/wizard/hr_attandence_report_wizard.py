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
            if rec.start_date > rec.end_date:
                raise ValidationError(_('Sorry, Start Date is not be greater than End Date...'))
            elif rec.start_date > rec.end_date:
                raise ValidationError(_('Sorry ,End Date Should Not be Future Date'))

    def generate_report(self):
        output = io.BytesIO()
        file_name = ('Daily Attendance Report')
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        header_style = workbook.add_format({
            'bold': True,
            'font_size' : 11,
            'align': 'center',
            'valign': 'vcenter',
            'border': 1
        })
        body_style = workbook.add_format({
            'border': 1,
            'align': 'center',
            'valign': 'vcenter',
            'font_size' : 9
        })
        worksheet = workbook.add_worksheet('Employee Attendance Report')

        # domain = [
        #     ('schedule_in', '>=', self.start_date),
        #     ('schedule_in', '<=', self.end_date),
        # ]
        
        # if self.employee_ids:
        #     domain.append(('employee_id', 'in', self.employee_ids.ids))   
            
        attendance_line = self.env['hr.attendance'].search([('employee_id', 'in', self.employee_ids.ids)])
        attendance_line = sorted(attendance_line, key=lambda x: x.employee_id.name) # Sort the employee name alphabetically

        # Convert the list to an Odoo recordset
        attendance_line_records = self.env['hr.attendance'].browse([line.id for line in attendance_line])
        
        # Initialize a dictionary to store the total checkouts for each employee and company
        checkout_count_by_employee_and_company = {}

        # Loop through the attendance records
        for record in attendance_line_records:
            employee_name = record.employee_id.name
            company_name = record.checkout_remarks

            # If the employee name is not already in the dictionary, add it
            if employee_name not in checkout_count_by_employee_and_company:
                checkout_count_by_employee_and_company[employee_name] = {}

            # Increment the checkout count for the specified company for the employee
            checkout_count_by_employee_and_company[employee_name][company_name] = checkout_count_by_employee_and_company[employee_name].get(company_name, 0) + 1

        company1_total = 0
        company2_total = 0
        company3_total = 0
        company4_total = 0
        company5_total = 0
        company6_total = 0
        company7_total = 0
        company8_total = 0
        company9_total = 0

        worksheet.set_column(0 ,0,7)
        worksheet.set_column(1, 1, 25)
        worksheet.set_column(2, 5, 16)
        worksheet.set_column(6, 33, 25)

        # Write column headers
        row=0
        col=0
        worksheet.set_row(row, 25)
        worksheet.freeze_panes(1,0)
        columns = ['S.No', 'Employee', 'Schedule In', 'Schedule Out', 'Actual Time In', 'Actual Time Out',
            'Regular Hour', 'Absent Hour', 'OB Hour', 'LWP Hour', 'LWOP Hour', 'Late', 'Undertime',
            'Rest Day', 'Special Holiday', 'Regular Holiday', 'Night Shift Differential',
            'Regular Overtime', 'Rest Day Overtime', 'Special Holiday Overtime',
            'Regular Holiday Overtime', 'Night Differential Overtime', 'Check-in Remarks',
            'Check-out Remarks', 'Asian Cantina Inc.', 'HGRCG Inc.', 'Nikkei Global City Inc.',
            'Nikkei Newport Inc.', 'Nikkei Podium Inc.', 'Nikkei Rada Inc.', 'Nikkei Robata BGC Inc.',
            'Nikkei Rockwell Inc.', 'Terraza Shang BGC Inc.', 'Remarks']

        for column in columns:
            worksheet.write(row, col, column, header_style)
            col += 1
        
        # Generate attendance data
        row = 1
        no = 1
        for res in attendance_line:
            schedule_in = res.schedule_in and context_timestamp(self, fields.Datetime.from_string(res.schedule_in)) or ''
            schedule_out = res.schedule_out and context_timestamp(self, fields.Datetime.from_string(res.schedule_out)) or ''
            if res.check_in or res.check_out:
                check_in = res.check_in and context_timestamp(self, fields.Datetime.from_string(res.check_in)) or ''
                check_out = res.check_out and context_timestamp(self, fields.Datetime.from_string(res.check_out)) or ''
                is_hide_check_time = res.is_hide_check_time
            else:
                is_hide_check_time = True

            col = 0
            worksheet.write(row, col, no, body_style)
            col += 1
            worksheet.write(row, col , res.employee_id.name, body_style)
            col += 1
            worksheet.write(row, col , schedule_in.strftime("%Y-%m-%d %H:%M:%S") if schedule_in else '' , body_style)
            col += 1
            worksheet.write(row, col , schedule_out.strftime("%Y-%m-%d %H:%M:%S") if schedule_out else ''  , body_style)
            col += 1
            if is_hide_check_time:
                col +=2
            else:
                worksheet.write(row, col , check_in.strftime("%Y-%m-%d %H:%M:%S") if check_in else '', body_style)
                col += 1
                worksheet.write(row, col , check_out.strftime("%Y-%m-%d %H:%M:%S") if check_out else '', body_style)
                col += 1
            worksheet.write(row, col , res.worked_hours, body_style)
            col += 1
            worksheet.write(row, col , res.absent_hours, body_style)
            col += 1
            worksheet.write(row, col , res.ob_hours, body_style)
            col += 1
            worksheet.write(row, col , res.leave_hours, body_style)
            col += 1
            worksheet.write(row, col , res.leave_wop_hours, body_style)
            col += 1
            worksheet.write(row, col , res.late_hours, body_style)
            col += 1
            worksheet.write(row, col , res.undertime_hours, body_style)
            col += 1
            worksheet.write(row, col , res.rest_day_hours, body_style)
            col += 1
            worksheet.write(row, col , res.sp_holiday_hours, body_style)
            col += 1
            worksheet.write(row, col , res.reg_holiday_hours, body_style)
            col += 1
            worksheet.write(row, col , res.night_diff_hours, body_style)
            col += 1
            worksheet.write(row, col , res.overtime_hours, body_style)
            col += 1
            worksheet.write(row, col , res.rest_day_ot_hours, body_style)
            col += 1
            worksheet.write(row, col , res.sp_hday_ot_hours, body_style)
            col += 1
            worksheet.write(row, col , res.reg_hday_ot_hours, body_style)
            col += 1
            worksheet.write(row, col , res.night_diff_ot_hours, body_style)
            col += 1
            if not res.checkin_remarks:
                worksheet.write(row, col , "No Check-in Remarks", body_style)
                col += 1
            else:
                worksheet.write(row, col , res.checkin_remarks, body_style)
                col += 1
            if not res.checkout_remarks:
                worksheet.write(row, col , "No Check-out Remarks", body_style)
                col += 1
            else:
                worksheet.write(row, col, res.checkout_remarks, body_style)
                col += 1
            if res.checkout_remarks == 'Asian Cantina Inc.' and res.check_in >= self.start_date and res.check_in <= self.end_date:
                for employee_name, company_count in checkout_count_by_employee_and_company.items():
                    for company_name, checkout_count in company_count.items():
                        if res.employee_id.name == employee_name:
                            worksheet.write(row, col, checkout_count, body_style)
                col += 1
            else:
                worksheet.write(row, col, 0, body_style)
                col += 1
            if res.checkout_remarks == 'HGRCG Inc.' and res.check_in >= self.start_date and res.check_in <= self.end_date:
                for employee_name, company_count in checkout_count_by_employee_and_company.items():
                    for company_name, checkout_count in company_count.items():
                        if res.employee_id.name == employee_name:
                            worksheet.write(row, col, checkout_count, body_style)
                col += 1
            else:
                worksheet.write(row, col, 0, body_style)
                col += 1   
            if res.checkout_remarks == 'Nikkei Global City Inc.' and res.check_in >= self.start_date and res.check_in <= self.end_date:
                for employee_name, company_count in checkout_count_by_employee_and_company.items():
                    for company_name, checkout_count in company_count.items():
                        if res.employee_id.name == employee_name:
                            worksheet.write(row, col, checkout_count, body_style)
                col += 1
            else:
                worksheet.write(row, col, 0, body_style)
                col += 1
            if res.checkout_remarks == 'Nikkei Newport Inc.' and res.check_in >= self.start_date and res.check_in <= self.end_date:
                for employee_name, company_count in checkout_count_by_employee_and_company.items():
                    for company_name, checkout_count in company_count.items():
                        if res.employee_id.name == employee_name:
                            worksheet.write(row, col, checkout_count, body_style)
                col += 1
            else:
                worksheet.write(row, col, 0, body_style)
                col += 1
            if res.checkout_remarks == 'Nikkei Podium Inc.' and res.check_in >= self.start_date and res.check_in <= self.end_date:
                for employee_name, company_count in checkout_count_by_employee_and_company.items():
                    for company_name, checkout_count in company_count.items():
                        if res.employee_id.name == employee_name:
                            worksheet.write(row, col, checkout_count, body_style)
                col += 1
            else:
                worksheet.write(row, col, 0, body_style)
                col += 1
            if res.checkout_remarks == 'Nikkei Rada Inc.' and res.check_in >= self.start_date and res.check_in <= self.end_date:
                for employee_name, company_count in checkout_count_by_employee_and_company.items():
                    for company_name, checkout_count in company_count.items():
                        if res.employee_id.name == employee_name:
                            worksheet.write(row, col, checkout_count, body_style)
                col += 1
            else:
                worksheet.write(row, col, 0, body_style)
                col += 1
            if res.checkout_remarks == 'Nikkei Robata BGC Inc.' and res.check_in >= self.start_date and res.check_in <= self.end_date:
                for employee_name, company_count in checkout_count_by_employee_and_company.items():
                    for company_name, checkout_count in company_count.items():
                        if res.employee_id.name == employee_name:
                            worksheet.write(row, col, checkout_count, body_style)
                col += 1
            else:
                worksheet.write(row, col, 0, body_style)
                col += 1
            if res.checkout_remarks == 'Nikkei Rockwell Inc.' and res.check_in >= self.start_date and res.check_in <= self.end_date:
                for employee_name, company_count in checkout_count_by_employee_and_company.items():
                    for company_name, checkout_count in company_count.items():
                        if res.employee_id.name == employee_name:
                            worksheet.write(row, col, checkout_count, body_style)
                col += 1
            else:
                worksheet.write(row, col, 0, body_style)
                col += 1
            if res.checkout_remarks == 'Terraza Shang BGC Inc.' and res.check_in >= self.start_date and res.check_in <= self.end_date:
                for employee_name, company_count in checkout_count_by_employee_and_company.items():
                    for company_name, checkout_count in company_count.items():
                        if res.employee_id.name == employee_name:
                            worksheet.write(row, col, checkout_count, body_style)
                col += 1
            else:
                worksheet.write(row, col, 0, body_style)
                col += 1
            worksheet.write(row, col , res.remarks, body_style)
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
