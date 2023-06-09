# Copyright 2019 Eficent Business and IT Consulting Services S.L.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
# -*- coding: utf-8 -*-
from odoo import api, models, fields
from odoo.addons import decimal_precision as dp
<<<<<<< HEAD
from odoo.exceptions import ValidationError

import math
=======
from odoo.exceptions import ValidationError, UserError
>>>>>>> ea0989ff28d984b3f987c0ea8192691d52dea7c3

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

    # @api.multi
    # def attendance_action_change(self):
    #     res = super(HrEmployee, self).attendance_action_change()
    #     location = self.env.context.get('attendance_location', False)
    #     if location:
    #         if self.attendance_state == 'checked_in':
    #             if self.check_in_latitude == location[0] and self.check_in_longitude == location[1]:
    #                 location_mismatched = False
    #             else:
    #                 location_mismatched = True
    #             res.write({
    #                 'check_in_latitude': location[0],
    #                 'check_in_longitude': location[1],
    #                 'checkin_location_mismatched': location_mismatched
    #             })
    #         else:
    #             if self.check_out_latitude == location[0] and self.check_out_longitude == location[1]:
    #                 location_mismatched = False
    #             else:
    #                 location_mismatched = True
    #             res.write({
    #                 'check_out_latitude': location[0],
    #                 'check_out_longitude': location[1],
    #                 'checkout_location_mismatched':location_mismatched
    #             })
    #     return res

    @api.multi
    def attendance_action_change(self):
        res = super(HrEmployee, self).attendance_action_change()
        location = self.env.context.get('attendance_location', False)
        remarks = self.env.context.get('remarks', False)
        if location:
<<<<<<< HEAD
            if self.attendance_state == 'checked_out':
                res.write({
                    'check_out_latitude': location[0],
                    'check_out_longitude': location[1],
                    'checkin_location_mismatched': self.calculate_distance(self.check_in_latitude, self.check_in_longitude, self.check_out_latitude, self.check_out_longitude),
                    'checkout_location_mismatched': self.check_in_latitude != self.check_out_latitude and self.check_in_longitude != self.check_out_longitude,
=======
            if self.attendance_state == 'checked_in':
                if self.check_in_latitude == location[0] and self.check_in_longitude == location[1]:
                    location_mismatched = False
                else:
                    location_mismatched = True
                res.write({
                    'check_in_latitude': location[0],
                    'check_in_longitude': location[1],
                    'checkin_location_mismatched': location_mismatched,
                    'checkin_remarks': remarks
                })
            else:
                if self.check_out_latitude == location[0] and self.check_out_longitude == location[1]:
                    location_mismatched = False
                else:
                    location_mismatched = True
                res.write({
                    'check_out_latitude': location[0],
                    'check_out_longitude': location[1],
                    'checkout_location_mismatched': location_mismatched,
                    'checkout_remarks': remarks
>>>>>>> ea0989ff28d984b3f987c0ea8192691d52dea7c3
                })
                
            elif self.attendance_state == 'checked_in':
                res.write({
                    'check_in_latitude': location[0],
                    'check_in_longitude': location[1],
                })

        return res
    
    def calculate_distance(self, lat1, lon1, lat2, lon2):
        # Convert degrees to radians
        lat1 = math.radians(lat1)
        lon1 = math.radians(lon1)
        lat2 = math.radians(lat2)
        lon2 = math.radians(lon2)

        # Haversine formula
        dlon = lon2 - lon1
        dlat = lat2 - lat1
        a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
        distance = 6371 * c * 1000  # Multiply by Earth's radius to get distance in meters

        # Checks if the two locations' distance is below 10 meters
        if distance < 10:
            # Return 'False' so the location mismatch will be flagged as 'False'
            return False
        else:
            return True

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