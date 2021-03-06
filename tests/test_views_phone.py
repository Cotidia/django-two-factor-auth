# -*- coding: utf-8 -*-

try:
    from unittest import mock
except ImportError:
    import mock

from django.conf import settings
from django.core.urlresolvers import reverse
from django.shortcuts import resolve_url
from django.test import TestCase
from django.test.utils import override_settings
from django.utils import six
from django_otp.oath import totp
from django_otp.util import random_hex

from two_factor.models import PhoneDevice
from two_factor.utils import backup_phones
from two_factor.validators import validate_international_phonenumber

from .utils import UserMixin


class PhoneSetupTest(UserMixin, TestCase):
    def setUp(self):
        super(PhoneSetupTest, self).setUp()
        self.user = self.create_user()
        self.enable_otp()
        self.login_user()

    def test_form(self):
        response = self.client.get(reverse('two_factor:phone_create'))
        self.assertContains(response, 'Number:')

    def _post(self, data=None):
        return self.client.post(reverse('two_factor:phone_create'), data=data)

    @mock.patch('two_factor.gateways.fake.Fake')
    @override_settings(
        TWO_FACTOR_SMS_GATEWAY='two_factor.gateways.fake.Fake',
        TWO_FACTOR_CALL_GATEWAY='two_factor.gateways.fake.Fake',
    )
    def test_setup(self, fake):
        response = self._post({'phone_setup_view-current_step': 'setup',
                               'setup-number': '',
                               'setup-method': ''})
        self.assertEqual(response.context_data['wizard']['form'].errors,
                         {'method': ['This field is required.'],
                          'number': ['This field is required.']})

        response = self._post({'phone_setup_view-current_step': 'setup',
                               'setup-number': '+31101234567',
                               'setup-method': 'call'})
        self.assertContains(response, 'We\'ve sent a token to your phone')
        device = response.context_data['wizard']['form'].device
        fake.return_value.make_call.assert_called_with(
            device=device, token='%06d' % totp(device.bin_key))

        response = self._post({'phone_setup_view-current_step': 'validation',
                               'validation-token': '123456'})
        self.assertEqual(response.context_data['wizard']['form'].errors,
                         {'token': ['Entered token is not valid.']})

        response = self._post({'phone_setup_view-current_step': 'validation',
                               'validation-token': totp(device.bin_key)})
        self.assertRedirects(response, resolve_url(settings.LOGIN_REDIRECT_URL))
        phones = self.user.phonedevice_set.all()
        self.assertEqual(len(phones), 1)
        self.assertEqual(phones[0].name, 'backup')
        self.assertEqual(phones[0].number.as_e164, '+31101234567')
        self.assertEqual(phones[0].key, device.key)

    @mock.patch('two_factor.gateways.fake.Fake')
    @override_settings(
        TWO_FACTOR_SMS_GATEWAY='two_factor.gateways.fake.Fake',
        TWO_FACTOR_CALL_GATEWAY='two_factor.gateways.fake.Fake',
    )
    def test_number_validation(self, fake):
        response = self._post({'phone_setup_view-current_step': 'setup',
                               'setup-number': '123',
                               'setup-method': 'call'})
        self.assertEqual(
            response.context_data['wizard']['form'].errors,
            {'number': [six.text_type(validate_international_phonenumber.message)]})


class PhoneDeleteTest(UserMixin, TestCase):
    def setUp(self):
        super(PhoneDeleteTest, self).setUp()
        self.user = self.create_user()
        self.backup = self.user.phonedevice_set.create(name='backup', method='sms', number='+1')
        self.default = self.user.phonedevice_set.create(name='default', method='call', number='+1')
        self.login_user()

    def test_delete(self):
        response = self.client.post(reverse('two_factor:phone_delete',
                                            args=[self.backup.pk]))
        self.assertRedirects(response, resolve_url(settings.LOGIN_REDIRECT_URL))
        self.assertEqual(list(backup_phones(self.user)), [])

    def test_cannot_delete_default(self):
        response = self.client.post(reverse('two_factor:phone_delete',
                                            args=[self.default.pk]))
        self.assertContains(response, 'was not found', status_code=404)


class PhoneDeviceTest(UserMixin, TestCase):
    def test_verify(self):
        for no_digits in (6, 8):
            with self.settings(TWO_FACTOR_TOTP_DIGITS=no_digits):
                device = PhoneDevice(key=random_hex().decode())
                self.assertFalse(device.verify_token(-1))
                self.assertFalse(device.verify_token('foobar'))
                self.assertTrue(device.verify_token(totp(device.bin_key, digits=no_digits)))

    def test_verify_token_as_string(self):
        """
        The field used to read the token may be a CharField,
        so the PhoneDevice must be able to validate tokens
        read as strings
        """
        for no_digits in (6, 8):
            with self.settings(TWO_FACTOR_TOTP_DIGITS=no_digits):
                device = PhoneDevice(key=random_hex().decode())
                self.assertTrue(device.verify_token(str(totp(device.bin_key, digits=no_digits))))

    def test_unicode(self):
        device = PhoneDevice(name='unknown')
        self.assertEqual('unknown (None)', str(device))

        device.user = self.create_user()
        self.assertEqual('unknown (bouke@example.com)', str(device))
