#! -*- coding:utf-8 -*-

from odoo import models, fields, api
from odoo.tools.translate import _
from odoo.exceptions import ValidationError,UserError

from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT as DATETIME_FORMAT
import pytz
from xlrd import open_workbook,xldate_as_tuple
from xlrd import open_workbook
from datetime import timedelta,date,datetime,time
from dateutil.relativedelta import *
from odoo.fields import Datetime
from dateutil.relativedelta import relativedelta

import re
import logging
import xlwt
import base64
import cStringIO
import StringIO

context_timestamp = Datetime.context_timestamp
from_string = Datetime.from_string
to_string = Datetime.to_string


def context_utc(date, timezone):
    """Returns date and time into utc format."""
    if not date:
        return ''

    if not timezone:
        timezone = 'UTC'

    tz = pytz.timezone(timezone)
    local_date = tz.localize(date, is_dst=None)
    utc_date = local_date.astimezone(pytz.utc)

    return utc_date


_logger = logging.getLogger(__name__)

class Timelogs(models.Model):
    _name = 'hr.timelog'
    _description = 'Timelogs'
    _order = 'id DESC'

    def utc_to_dt(self, to_convert):
        return pytz.utc.localize(datetime.datetime.strptime(to_convert, '%Y-%m-%d %H:%M:%S'))

    def rec_to_dt(self, to_convert):
        return datetime.datetime.strptime(to_convert, self.parser.datetime_format)

    def dt_to_rec(self, to_convert):
        return to_convert.strftime(self.parser.datetime_format)

    def rec_to_utc(self, to_convert):
        tz_name = self._context.get('tz') or self.env.user.tz
        if not tz_name:
            raise UserError(_('No timezone!\n\nConfigure your timezone\n\nClick your Profile Name>Preferences>Timezone(Asia/Manila)'))
        local = pytz.timezone(tz_name)
        local_dt_log = local.localize(to_convert, is_dst=None)
        return local_dt_log.astimezone(pytz.utc)

    def utc_to_rec(self, to_convert):
        tz_name = self._context.get('tz') or self.env.user.tz
        if not tz_name:
            raise UserError(_('No timezone!\n\nConfigure your timezone\n\nClick your Profile Name>Preferences>Timezone(Asia/Manila)'))
        local = pytz.timezone(tz_name)
        return to_convert.astimezone(local)

    @api.multi
    def load_file(self):
        timelogs = self.attachment_id and self.attachment_id.datas.decode('base64') or False
        return timelogs

    def get_attendance(self):
        timelogs = self.load_file()

        tz_name = self._context.get('tz') or self.env.user.tz
        if not tz_name:
            raise UserError(_('No timezone!\n\nConfigure your timezone\n\nClick your Profile Name>Preferences>Timezone(Asia/Manila)'))

        if not timelogs:
            raise ValidationError(_('No uploaded timelogs'))

        attendance_record = []
        res = timelogs.splitlines()
        del res[:self.parser.skip_row]
        for record in res:
            if record:
                try:
                    barcode = re.findall(self.parser.employeeid_regex, record)[self.parser.employeeid_index].strip()
                except:
                    pass

                try:
                    employee_id = self.env['hr.employee'].search([('barcode', '=', barcode)], limit=1).id
                except:
                    pass

                try:
                    datetime_string  = re.findall(self.parser.datetime_regex, record)[0].strip()
                    datetime_log =  datetime.datetime.strptime(datetime_string, self.parser.datetime_format)
                    local = pytz.timezone(tz_name)
                    local_dt_log = local.localize(datetime_log, is_dst=None)
                    utc_dt_log = local_dt_log.astimezone(pytz.utc)
                except:
                    pass

                try:
                    inout_type = re.findall(self.parser.inout_regex, record)[0].strip()
                except:
                    pass
   
                try:
                    attendance_record.append([utc_dt_log, barcode,  inout_type, local_dt_log, employee_id])
                except:
                    pass
        
        return attendance_record

    def import_attendance(self):
        error_string = ""
        attend_logs = sorted(self.get_attendance())
        
        for item in attend_logs:
            timelogs = {}
            if item[2] == self.parser.in_string:
                try:
                    employee_id = item[4]
                except:
                    employee_id = None
                
                values = {}
                values['timelog_id'] = self.id
                values['barcode'] = item[1],
                values['check_in'] = item[0]
                values['employee_id'] = employee_id

                
            #     out_id = self.env['hr.timelog.line'].search([('check_out', '=', False),
            #                                                 ('barcode','=', item[1]),
            #                                                 ('timelog_id', '=', self.id)],
            #                                                 limit=1)
            #     if out_id:
            #         error_string = error_string + "\nDid not add due to overlap: \n" + \
            #              "Record of id: " + str(item[1]) + \
            #             ", Check in: " + self.dt_to_rec(item[3]) + "\n"
            #
            #     if not out_id:
            #         self.env['hr.timelog.line'].create(values)
            #         _logger.error("created: " + str(values)+ " local: " + str(item[3]))
            #
            # elif item[2] == self.parser.out_string:
            #
            #     out_id = self.env['hr.timelog.line'].search([('check_out', '=', False),
            #                                                 ('barcode','=', item[1]),
            #                                                 ('timelog_id', '=', self.id)],
            #                                                 limit=1)
            #
            #     if out_id:
            #
            #         w = self.utc_to_dt(out_id.check_in)
            #         x = item[0] - w
            #         y = abs(x.total_seconds()) or 0
            #         if not y:
            #             y = 0
            #         z = self.parser.maxtime * 3600.0
            #
            #         if z-y < 0:
            #             attend_error = filter(lambda r: r[1] == item[1] and r[0] > w and r[0] < item[0], attend_logs)
            #             error_string = "logged hours of: " + str(y / 3600.0) + \
            #                 "\n" + "Suspect the following for errors:\n"
            #             for err_line in attend_error:
            #                 error_string = error_string + str(err_line[2]) + \
            #                 ": " + self.dt_to_rec(err_line[3]) + "\n"
            #
            #             out_id.write({'check_out' : item[0], 'for_checking' : True, 'remarks' : error_string})
            #
            #         else:
            #             out_id.write({'check_out' : item[0]})
            #
            #     if not out_id:
            #         error_string = error_string + "\nDid not add due to overlap: \n" + \
            #              "Record of id: " + str(item[1]) + \
            #             ", Check out: " + self.dt_to_rec(item[3]) + "\n"
        
    def get_attendance_rbt(self):
        timelogs = self.load_file()

        if not timelogs:
            raise ValidationError(_('No uploaded timelogs'))

        attendance_record = {}
        res = timelogs.splitlines()
    
        del res[:self.parser.skip_row]
        for record in res:

            if record:
            
                r = record.split()
                user_id = r[0]
                date_log = r[1]
                time_log = r[2]
                    
                #index1 = r[3]
                #index2 = r[4]

                key = user_id,date_log
                datetime_log = date_log + ' ' + time_log
                if key in attendance_record:
                
                    attendance_record[key].append(datetime_log)
                else:
                    attendance_record[key] = [datetime_log]

        return attendance_record

    def import_attendance_rbt(self):
        res = []
        for key, values in sorted(self.get_attendance_rbt().items(), key=lambda r:r[1]):
            timelogs = {}
            if values:

                barcode = key[0]
                check_in  = datetime.strptime(values[0], '%Y-%m-%d %H:%M:%S') #format '%m/%d/%Y %H:%M:%S'
                check_out = datetime.strptime(values[-1], '%Y-%m-%d %H:%M:%S') #format '%m/%d/%Y %H:%M:%S'
                tz_name = self._context.get('tz') or self.env.user.tz
                if not tz_name:
                    raise UserError(_('No timezone!\n\nConfigure your timezone\n\nClick your Profile Name>Preferences>Timezone(Asia/Manila)'))

                local = pytz.timezone(tz_name)
                local_dt_in = local.localize(check_in, is_dst=None)
                local_dt_out = local.localize(check_out, is_dst=None)

                utc_date_in = local_dt_in.astimezone(pytz.utc)
                utc_txn_date_in  = utc_date_in.strftime(DATETIME_FORMAT)

                utc_date_out = local_dt_out.astimezone(pytz.utc)
                utc_txn_date_out  = utc_date_out.strftime(DATETIME_FORMAT)

                employee_id = self.env['hr.employee'].search([('employee_num', '=', barcode)], limit=1).id
                if not employee_id:
                    continue
                timelogs['employee_id'] = employee_id or False
                timelogs['barcode'] = barcode
                timelogs['check_in'] = utc_txn_date_in
                timelogs['check_out'] = utc_txn_date_out
                timelogs['timelog_id'] = self.id
                res.append(timelogs)

        for record in res:
            domain = [
                ('barcode', '=', record['barcode']),
                ('check_in', '=', record['check_in']),
                ('check_out', '=', record['check_out']),
                ('timelog_id', '=', record['timelog_id'])]
            count = self.env['hr.timelog.line'].search_count(domain)
            if count > 0:
                continue
            self.env['hr.timelog.line'].create(record)

    # @api.onchange('attachment_id')
    # def onchange_attachment(self):
    #     self.name = self.attachment_id.name

    def action_import_attendance(self):
        if self.parser.input_type == 'first_in_out':
            self.import_attendance()
        elif self.parser.input_type == 'security':
            self.import_attendance_rbt()
        elif self.parser.input_type == 'first_in_out_a':
            self.create_attendance()

    @api.multi
    def action_process_attendance(self):
        """Process timelogs and generate employee attendances and timekeeping."""

        if not self.timelog_line:
            raise ValidationError(_('Unable to process timelogs without details.'))
        
        #if self.timelog_line.filtered(lambda r: not r.employee_id):
        #    raise ValidationError(_('Timelogs have no employee name.'))
        
        for log in self:
            attendance_obj = self.env['hr.attendance']
            missing_dates = []
            schedule_week_days = []
            for record in log.timelog_line.filtered(lambda r: r.employee_id):
                #Week Days
                work_time = self.env['hr.employee.schedule.work_time'].search([
                    ('employee_id', '=', record.employee_id.id),('state', '=', 'approved')],
                    order="priority", limit=1)
                schedule_week_days += work_time.work_time_lines.mapped('days_of_week')
                # we take the latest attendance before our check_in time and check it doesn't overlap with ours
                last_attendance_before_check_in = self.env['hr.attendance'].search([
                    ('employee_id', '=', record.employee_id.id),
                    ('check_in', '<=', record.check_in),
                    ('worked_hours', '=', 0),
                    ('ob_hours', '=', 0)
                ], order='check_in desc', limit=1)
                #new time-in between time-in and time-out 
                if last_attendance_before_check_in and last_attendance_before_check_in.check_out and last_attendance_before_check_in.check_out >= record.check_in:
                    #Overwrite absent attendance
                    vals = {}
                   
                    vals['check_in'] = record.check_in
                    vals['check_out'] = record.check_out 
                    vals['remarks'] = ''
                    vals['is_raw'] = True
                    
                    if last_attendance_before_check_in.is_absent:
                        vals['is_absent'] = False
                    last_attendance_before_check_in.write(vals)
                    continue
                
                if not record.check_out:
                    # if our attendance is "open" (no check_out), we verify there is no other "open" attendance
                    no_check_out_attendances = self.env['hr.attendance'].search([
                        ('employee_id', '=', record.employee_id.id),
                        ('check_out', '=', False),
                    ])
                    if no_check_out_attendances:
                        pass
                    
                else:
                    # we verify that the latest attendance with check_in time before our check_out time
                    # is the same as the one before our check_in time computed before, otherwise it overlaps
                    last_attendance_before_check_out = self.env['hr.attendance'].search([
                        ('employee_id', '=', record.employee_id.id),
                        ('check_in', '<', record.check_out),
                        ('worked_hours', '=', 0),
                        ('ob_hours', '=', 0)
                    ], order='check_in desc', limit=1)
                    #get last attendance before this new attendance
                    if last_attendance_before_check_out and last_attendance_before_check_in != last_attendance_before_check_out:
                        #Overwrite absent attendance
                        vals = {}
                        
                        vals['check_in'] = record.check_in
                        vals['check_out'] = record.check_out 
                        vals['remarks'] = ''
                        vals['is_raw'] = True
                        
                        if last_attendance_before_check_out.is_absent:
                            vals['is_absent'] = False
                            
                        last_attendance_before_check_out.write(vals)
                            
                        continue
                        
                cutoff_start = fields.Datetime.context_timestamp(self, fields.Datetime.from_string(log.payroll_period_id.start_date)) 
                cutoff_end = fields.Datetime.context_timestamp(self, fields.Datetime.from_string(log.payroll_period_id.end_date))
                
                dt_start = cutoff_start.replace(hour=0, minute=0, second=0)
                dt_end = cutoff_end.replace(hour=7, minute=0, second=0) + timedelta(days=1)
                check_in = fields.Datetime.context_timestamp(self, fields.Datetime.from_string(record.check_in))
                
                #Only process logs within the payroll cutoff and write it as raw log
                emp = attendance_obj.search([('employee_id','=',record.employee_id.id)])
                timelog_dates = [fields.Datetime.context_timestamp(self, fields.Datetime.from_string(x.check_in)).date() for x in log.timelog_line]
                delta = cutoff_end.date() - cutoff_start.date()
                date_set = set([cutoff_start.date() + timedelta(days=i) for i in range(delta.days + 1)])
                missing_dates = sorted(date_set - set(timelog_dates))
                if check_in.date() in timelog_dates:
                    check_emp = emp.filtered(lambda l: l.check_in and datetime.strptime(l.check_in, '%Y-%m-%d %H:%M:%S').date() == datetime.strptime(record.check_in, '%Y-%m-%d %H:%M:%S').date() and (l.worked_hours == 0 and l.ob_hours == 0))
                    if check_emp:
                        check_emp.write({'check_in': record.check_in,
                               'check_out': record.check_out,
                               'remarks': '',
                               'is_absent': False,
                            })
                    if not check_emp:
                        attendance_obj.create_attendance(record.employee_id, record.check_in, record.check_out)

            for val in log.timelog_line:
                for date in missing_dates:
                    if date.strftime('%A').lower() in schedule_week_days:
                        date = date.strftime('%Y-%m-%d %H:%M:%S')
                        attendance_id = attendance_obj.search([('employee_id','=',val.employee_id.id),
                                                               '|', ('check_in', '=', date),
                                                               ('check_out', '=', date)], limit=1)
                        if attendance_id.filtered(lambda l: l.worked_hours == 0 and l.ob_hours == 0):
                            attendance_id.write({'is_absent': True})
                        if not attendance_id:
                            attendance_obj.create_attendance(record.employee_id, date, date)
                attendance_id = attendance_obj.search([('employee_id','=',val.employee_id.id),('remarks','=','ABS')])
                for date in attendance_id:
                    if date.schedule_in == False:
                        date.unlink()

                attendance = attendance_obj.search([('employee_id','=',val.employee_id.id),('remarks','=','ABS')])
                for att in attendance:
                    check_in = datetime.strptime(val.check_in, '%Y-%m-%d %H:%M:%S')
                    check_in = check_in.strftime("%Y-%m-%d")
                    schedule_date = datetime.strptime(att.schedule_in, '%Y-%m-%d %H:%M:%S')
                    schedule_date = schedule_date.strftime("%Y-%m-%d")

                    if check_in == schedule_date:
                        att.unlink()

                    
#             Generate absences between payroll cut off
#             attendance_obj.make_absent(log.payroll_period_id.start_date, log.payroll_period_id.end_date)
        return self.write({'state': 'processed'})
   
    """start time logs default"""
    def get_timelog_attendances(self):
        """Get attendance logs from biometrics."""
        cutoff_start = fields.Datetime.context_timestamp(self, fields.Datetime.from_string(self.payroll_period_id.start_date))
        cutoff_end = fields.Datetime.context_timestamp(self, fields.Datetime.from_string(self.payroll_period_id.end_date))

        date_start = fields.Datetime.from_string(fields.Datetime.to_string(cutoff_start.replace(hour=0, minute=0, second=0)))
        date_end = fields.Datetime.from_string(fields.Datetime.to_string(cutoff_end.replace(hour=7, minute=0, second=0) + timedelta(days=1)))
        
        parsed_utc_start = self.rec_to_utc(date_start)
        parsed_utc_end = self.rec_to_utc(date_end)
        
        dt_start = fields.Datetime.to_string(parsed_utc_start)
        dt_end = fields.Datetime.to_string(parsed_utc_end)
       
        logs = self.env['logs.logs'].search([('checktime','>=', dt_start), 
                                             ('checktime','<=', dt_end)])
    
        time_logs = {}
        for rec in logs:
            date =  fields.Datetime.from_string(rec.checktime) #fields.Datetime.context_timestamp(self, rec.checktime).strftime('%Y-%d-%m')
            
            format_date = fields.Datetime.context_timestamp(self, date).strftime('%Y-%d-%m') #datetime.datetime.strptime(str(date),'%Y-%m-%d %H:%M:%S').strftime('%Y-%d-%m')
            
            if (rec.userid,format_date) in time_logs:
                time_logs[(rec.userid, format_date)].append(rec)
            else:
                time_logs[(rec.userid, format_date)] = [rec]
        
        records = []
        
        for emp_id, checktime in time_logs.items():     
            index_out = 0
            for index in range(len(checktime)):
                index_out += 1
                employee_id = emp_id[0]
                time_in = checktime[0]
                time_out = checktime[-index_out]
            
                check_in = [r.checktime for r in time_in]
                ensure_check_in = [r.checktype for r in time_in][0]
                check_out = [r.checktime for r in time_out]
                ensure_check_out = [r.checktype for r in time_out][0]
                
                if ensure_check_in == 'I' and ensure_check_out == 'O':
                    emp_name = self.env['hr.employee'].search([('barcode', '=', employee_id)], limit=1).id
                    if not employee_id:
                        continue
                    val = {
                    'employee_id': emp_name,
                    'timelog_id':self.id,\
                    'barcode' : employee_id,\
                    'check_in' : check_in[0],\
                    'check_out' : check_out[0]
                    }
                    records.append(val)
                    break 
                else:
                    pass
    
        return records
    
    def create_attendance(self):
        get_attendance = self.get_timelog_attendances()
        
        for records in get_attendance:
            self.env['hr.timelog.line'].create(records)
    
    """end time logs default"""
    def btn_import_attendance(self):
        self.action_import_attendance()

    @api.multi
    def btn_import_excel(self, data):
        self.timelog_line.unlink()
        try:
            inputx = StringIO.StringIO()
            inputx.write(base64.decodestring(self.upload_excel_file))
            book = open_workbook(file_contents=inputx.getvalue())
        except TypeError as e:
            raise ValidationError('No Excel Found')
        sheet = book.sheets()[0]

        rows = sheet.nrows


        class get_all_attendance_from_excel:
            @staticmethod
            def time_converstion(date, time_zone):
                if date:
                    time_in_out = datetime.strptime(str(date), '%Y-%m-%d %H:%M:%S')
                    local = pytz.timezone('Asia/Manila')
                    local_dt_log = local.localize(time_in_out, is_dst=None)
                    res = local_dt_log.astimezone(pytz.utc)
                    return res
                else:
                    return False

            @staticmethod
            def convert_excel_time(time):
                if time > 1:
                    time = time % 1
                seconds = round(time * 86400)
                minutes, seconds = divmod(seconds, 60)
                hours, minutes = divmod(minutes, 60)
                if hours > 24:
                    raise ValidationError("Error In Time format %d" % hours)
                return "%d:%d:%d" % (hours, minutes, seconds)

            @staticmethod
            def check_time_format(time_excel):
                time = False
                if time_excel:
                    try:
                        res = bool(datetime.strptime(time_excel, '%Y-%m-%d %H:%M:%S'))
                    except ValueError:
                        raise ValidationError(
                            ("Incorrect date format, should be YYYY-MM-DD HH:MM:SS for %s in row %d") % (str(time_excel), (i + 1)))
                    hours = re.findall(r'\d+', time_excel.split(' ')[-1])[0]
                    if int(hours) > 24:
                        raise ValidationError(
                            "Error in Time Value %s , should be less than 24hrs military time in row  %d" % (time_out_excel, (i + 1)))
                    if int(hours) == 24:
                        time = "23:59:59"

                    else:
                        time = time_excel
                return time

        timelog_data = []
        tz_name = self._context.get('tz') or self.env.user.tz
        for i in range(rows):
            if i != 0:
                emp = str(sheet.cell(i,0).value)
                if not emp:
                    raise ValidationError("No employee number found for row %s"%(i + 1))
                emp_id = self.env['hr.employee'].search([('employee_num','=',emp)],limit=1)
                if not emp_id:
                    raise ValidationError("No employee number found for %s"%(emp))
                IN_TIME = sheet.cell(i, 4).value
                OUT_TIME = sheet.cell(i, 5).value
                if isinstance(IN_TIME, float):
                    in_time = get_all_attendance_from_excel.convert_excel_time(IN_TIME)
                else:
                    time_in_excel = IN_TIME
                    result = get_all_attendance_from_excel.check_time_format(time_in_excel)
                    in_time = result
                if isinstance(OUT_TIME, float):
                    out_time = get_all_attendance_from_excel.convert_excel_time(OUT_TIME)
                else:
                    time_out_excel = OUT_TIME
                    result = get_all_attendance_from_excel.check_time_format(time_out_excel)
                    out_time = result

                time_in = get_all_attendance_from_excel.time_converstion(in_time, tz_name)
                time_out = get_all_attendance_from_excel.time_converstion(out_time, tz_name)

                timelog_data.append({
                    'barcode' : emp,
                    'remarks' : sheet.cell(i, 6).value,
                    'employee_id' : emp_id.id,
                    'check_in' : time_in,
                    'check_out' : time_out,
                    })
        self.timelog_line = timelog_data

    def btn_process(self):
        self.action_process_attendance()

    @api.multi
    def action_make_draft(self):
        return self.write({'state': 'draft'})

    def btn_make_draft(self):
        self.action_make_draft()

    name = fields.Char('Name', size=16, required=True)
    payroll_period_id = fields.Many2one('hr.payroll.period_line', 'Payroll Period', required=True, ondelete='restrict')
    attachment_id = fields.Many2one('ir.attachment', 'Timelogs', requried=True)
    timelog_line = fields.One2many('hr.timelog.line', 'timelog_id', 'Timelogs Line')
    state = fields.Selection([('draft', 'Draft'),
                              ('approved', 'Approved'),
                              ('processed', 'Processed')], 'State', default='draft')
    company_id = fields.Many2one('res.company', 'Company', default=lambda self:self.env.user.company_id.id,)
    parser = fields.Many2one('hr.timelog.parser', 'Parser')
    error_log = fields.Text()
    upload_excel_file = fields.Binary(string='Upload Excel File', attachment=True)
    upload_file_name = fields.Char('File Name')
    excel_format = fields.Many2one('hr.timelog.parser',string='Excel Format')



class TimelogsLine(models.Model):
    _name = 'hr.timelog.line'
    _description = 'Timelog Line'

    timelog_id = fields.Many2one('hr.timelog', 'Timelogs', required=True)
    employee_id = fields.Many2one('hr.employee', 'Employee Name')
    barcode = fields.Char('Employee Number', size=16, required=True)
    check_in = fields.Datetime('Time In')
    check_out = fields.Datetime('Time Out')
    company_id = fields.Many2one('res.company', 'Company', default=lambda self:self.env.user.company_id.id,)
    for_checking = fields.Boolean('Error')
    remarks = fields.Text("Remarks")

class Attachments(models.Model):
    _inherit = 'ir.attachment'

    timelog = fields.Boolean('Timelog', help="If a timelog file")

class TimelogParser(models.Model):
    _name = 'hr.timelog.parser'

    name = fields.Char()
    skip_row = fields.Integer()
    datetime_format = fields.Text()
    datetime_regex = fields.Text()
    employeeid_regex = fields.Text()
    employeeid_index = fields.Integer()
    inout_regex = fields.Text()
    in_string = fields.Text()
    out_string = fields.Text()
    maxtime = fields.Float(string='Time')
    input_type = fields.Selection([('first_in_out','First In, First Out(M)'),
                                   ('first_in_out_a', 'First In, First Out(A)'),
                                   ('security', 'Security Door')
                                   ], string='Input type')
