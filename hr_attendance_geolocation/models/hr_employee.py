# Copyright 2019 Eficent Business and IT Consulting Services S.L.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, models, fields
from odoo.addons import decimal_precision as dp

UNIT = dp.get_precision("Location")


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    @api.multi
    def attendance_manual(self, next_action, entered_pin=False,
                          location=False):
        res = super(HrEmployee, self.with_context(
            attendance_location=location)).attendance_manual(
            next_action, entered_pin)
        return res

    @api.multi
    def attendance_action_change(self):
        res = super(HrEmployee, self).attendance_action_change()
        location = self.env.context.get('attendance_location', False)
        if location:
            if self.attendance_state == 'checked_in':
                if self.check_in_latitude == location[0] and self.check_in_longitude == location[1]:
                    location_mismatched = False
                else:
                    location_mismatched = True
                res.write({
                    'check_in_latitude': location[0],
                    'check_in_longitude': location[1],
                    'checkin_location_mismatched': location_mismatched
                })
            else:
                if self.check_out_latitude == location[0] and self.check_out_longitude == location[1]:
                    location_mismatched = False
                else:
                    location_mismatched = True
                res.write({
                    'check_out_latitude': location[0],
                    'check_out_longitude': location[1],
                    'checkout_location_mismatched':location_mismatched
                })
        return res

    check_in_latitude = fields.Float(
        "Check-in Latitude",
        digits=UNIT,
    )
    check_in_longitude = fields.Float(
        "Check-in Longitude",
        digits=UNIT,
    )
    check_out_latitude = fields.Float(
        "Check-out Latitude",
        digits=UNIT,
    )
    check_out_longitude = fields.Float(
        "Check-out Longitude",
        digits=UNIT,
    )