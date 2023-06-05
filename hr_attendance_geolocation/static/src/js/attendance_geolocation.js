odoo.define('hr_attendance_geolocation.attendances_geolocation', function (require) {
    "use strict";

    var MyAttendances = require('hr_attendance.my_attendances');
    var KioskConfirm = require('hr_attendance.kiosk_confirm');
    var Model = require('web.Model');
    var Dialog = require('web.Dialog');

    MyAttendances.include({
        init: function (parent, action) {
            this._super.apply(this, arguments);
            this.location = [null, null];
            this.errorCode = null;
        },
        update_attendance: function () {
            var self = this;
            var options = {
                enableHighAccuracy: true,
                timeout: 5000,
                maximumAge: 0
            };
            if (navigator.geolocation) {
                navigator.geolocation.getCurrentPosition(self._manual_attendance.bind(self), self._getPositionError, options);
            }
        },
        _manual_attendance: function (position) {
            var self = this;
            console.log("2777777777777777")
            var hr_employee = new Model('hr.employee');
            var res_company = new Model('res.company');

            // Retrieve the list of companies from the server
            res_company.call('search_read', [], { fields: ['id', 'name'] }).then(function (result) {
                if (!result || !Array.isArray(result)) {
                    console.error("Invalid server response:", result);
                    return;
                }

                var companies = result;

                // Create a dropdown menu with the list of companies
                var $dropdown = $('<select>');
                companies.forEach(function (company) {
                    $dropdown.append($('<option>').val(company.id).text(company.name));
                });

                // Open the select company dialog
                new Dialog(self, {
                    title: 'Select Company',
                    size: 'medium',
                    $content: $dropdown,
                    buttons: [{
                        text: 'Confirm',
                        classes: 'btn-primary',
                        click: function () {
                            var selectedCompanyId = parseInt($dropdown.val()); // Convert to integer
                            res_company.call('read', [selectedCompanyId, ['name']]).then(function (result) {
                                var companyName = result[0].name;

                                // Assign value to remarks variable
                                var remarks = companyName;

                                // Perform attendance action with the selected company
                                hr_employee.call('attendance_manual', [
                                    [self.employee.id],
                                    'hr_attendance.hr_attendance_action_my_attendances',
                                    self.$('.o_hr_attendance_PINbox').val(),
                                    [position.coords.latitude, position.coords.longitude],
                                    {
                                        'remarks': remarks,
                                    }]).then(function (result) {
                                        if (result.action) {
                                            self.do_action(result.action);
                                        } else if (result.warning) {
                                            self.do_warn(result.warning);
                                        }
                                        console.log("Selected Company:", remarks);
                                    });
                            });
                            this.close();
                        }
                    }, {
                        text: 'Cancel',
                        click: function () {
                            // Perform any required cleanup or behaviors when attendance is canceled
                            self.$('.o_hr_attendance_PINbox').val('');

                            // Display notification
                            self.do_notify('Attendance Canceled', 'Your attendance has been canceled.');

                            // Close the dialog
                            this.close();
                        }
                    }]
                }).open();
            });


            //             this._rpc({
            //                 model: 'hr.employee',
            //                 method: 'attendance_manual',
            //                 args: [[self.employee.id], 'hr_attendance.hr_attendance_action_my_attendances', null, [position.coords.latitude, position.coords.longitude]],
            //             })
            //             hr_employee.then(function(result) {
            //                 if (result.action) {
            //                     self.do_action(result.action);
            //                 } else if (result.warning) {
            //                     self.do_warn(result.warning);
            //                 }
            //             });
        },
        _getPositionError: function (error) {
            console.warn('ERROR(${error.code}): ${error.message}');
        },
    });

    KioskConfirm.include({
        events: _.extend(KioskConfirm.prototype.events, {
            "click .o_hr_attendance_sign_in_out_icon": _.debounce(function () {
                this.update_attendance();
            }, 200, true),
            "click .o_hr_attendance_pin_pad_button_ok": _.debounce(function () {
                this.pin_pad = true;
                this.update_attendance();
            }, 200, true),
        }),
        init: function (parent, action) {
            this._super.apply(this, arguments);
            this.pin_pad = false;
        },
        update_attendance: function () {
            var self = this;
            var options = {
                enableHighAccuracy: true,
                timeout: 5000,
                maximumAge: 0
            };
            if (navigator.geolocation) {
                navigator.geolocation.getCurrentPosition(self._manual_attendance.bind(self), self._getPositionError, options);
            }
        },
        _manual_attendance: function (position) {
            var self = this;
            if (this.pin_pad) {
                this.$('.o_hr_attendance_pin_pad_button_ok').attr("disabled", "disabled");
            }
            console.log("8777777777777777")
            var hr_employee = new Model('hr.employee');
            hr_employee.call('attendance_manual',
                [[this.employee_id], this.next_action, this.$('.o_hr_attendance_PINbox').val(), [position.coords.latitude, position.coords.longitude]]).then(function (result) {
                    if (result.action) {
                        self.do_action(result.action);
                    } else if (result.warning) {
                        self.do_warn(result.warning);
                        if (self.pin_pad) {
                            self.$('.o_hr_attendance_PINbox').val('');
                            setTimeout(function () {
                                self.$('.o_hr_attendance_pin_pad_button_ok').removeAttr("disabled");
                            }, 500);
                        }
                        self.pin_pad = false;
                    }
                });

            //             this._rpc({
            //                 model: 'hr.employee',
            //                 method: 'attendance_manual',
            //                 args: [[this.employee_id], this.next_action, this.$('.o_hr_attendance_PINbox').val(), [position.coords.latitude, position.coords.longitude]],
            //             })
            //             .then(function(result) {
            //                 if (result.action) {
            //                     self.do_action(result.action);
            //                 } else if (result.warning) {
            //                     self.do_warn(result.warning);
            //                     if (self.pin_pad) {
            //                         self.$('.o_hr_attendance_PINbox').val('');
            //                         setTimeout( function() {
            //                             self.$('.o_hr_attendance_pin_pad_button_ok').removeAttr("disabled");
            //                         }, 500);
            //                     }
            //                     self.pin_pad = false;
            //                 }
            //             });
        },
        _getPositionError: function (error) {
            console.warn('ERROR(${error.code}): ${error.message}');
        },
    });

});
