# -*- coding: utf-8 -*-
{
    'name': 'Human Resource Information System',
    'version': '1.1',
    'category': 'Human Resource',
    'author': 'MM',
    'summary': "Recruitment, Payroll, Attendance, Leaves",
    'description': """ """,
    'website': '',
    'images': [

    ],
    'depends': [
        'base',
        'hris',
        'hr_holidays',

    ],
    'data': [
        'data/cron.xml',
        'security/ir.model.access.csv',
        'views/hr_leaves.xml',
        'views/hr_employee.xml',
        'views/leaves_benefits_policy.xml',
        'report/leaves_conversion_payslip.xml',

    ],

    'installable': True,
    'application': True,
    'auto_install': False,
}
