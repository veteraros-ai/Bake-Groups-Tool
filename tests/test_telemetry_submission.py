# -*- coding: utf-8 -*-
from __future__ import print_function

import os
import sys
import unittest

try:
    from urllib.parse import parse_qs
except ImportError:
    from urlparse import parse_qs


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_DIR = os.path.join(ROOT, "Bake_Groups")
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import bg_telemetry


FORM_CONTEXT = b"""<html><body><form id=\"mG61Hd\">
<input type=\"hidden\" name=\"fvv\" value=\"1\">
<input type=\"hidden\" name=\"partialResponse\" value=\"[null,null,&quot;token-123&quot;]\">
<input type=\"hidden\" name=\"pageHistory\" value=\"0\">
<input type=\"hidden\" name=\"fbzx\" value=\"token-123\">
<input type=\"hidden\" name=\"submissionTimestamp\" value=\"-1\">
</form></body></html>"""


class FakeResponse(object):
    def __init__(self, body, url, code=200):
        self._body = body
        self._url = url
        self._code = code
        self.closed = False

    def getcode(self):
        return self._code

    def geturl(self):
        return self._url

    def read(self):
        return self._body

    def close(self):
        self.closed = True


class TelemetrySubmissionTests(unittest.TestCase):
    def setUp(self):
        self.original_urlopen = bg_telemetry.urlopen

    def tearDown(self):
        bg_telemetry.urlopen = self.original_urlopen

    def _install_responses(self, post_body, post_url=None):
        calls = []
        responses = [
            FakeResponse(FORM_CONTEXT, bg_telemetry.FORM_VIEW_URL),
            FakeResponse(
                post_body,
                post_url or bg_telemetry.FORM_URL,
            ),
        ]

        def fake_urlopen(request, timeout=None):
            calls.append((request, timeout))
            return responses[len(calls) - 1]

        bg_telemetry.urlopen = fake_urlopen
        return calls, responses

    def test_post_includes_live_form_context(self):
        calls, responses = self._install_responses(
            b"<html><title>BG Telemetry</title><div>Recorded</div></html>")

        result = bg_telemetry._post(
            "client-123", "1.4.4", "update", "2027", "en", "windows-x64")

        self.assertTrue(result)
        self.assertEqual(len(calls), 2)
        payload = parse_qs(calls[1][0].data.decode("utf-8"))
        self.assertEqual(payload["fbzx"], ["token-123"])
        self.assertEqual(payload["partialResponse"], ['[null,null,"token-123"]'])
        self.assertEqual(payload[bg_telemetry.FIELD_CLIENT_ID], ["client-123"])
        self.assertEqual(payload[bg_telemetry.FIELD_VERSION], ["1.4.4"])
        self.assertEqual(payload[bg_telemetry.FIELD_PRODUCT], ["maya"])
        self.assertTrue(all(response.closed for response in responses))

    def test_post_rejects_form_redisplay_with_http_200(self):
        self._install_responses(
            b'<html><form action="/formResponse" id="mG61Hd">'
            b'<input name="entry.262576988"></form></html>')

        result = bg_telemetry._post(
            "client-123", "1.4.4", "update", "2027", "en", "windows-x64")

        self.assertFalse(result)

    def test_post_rejects_non_form_response_redirect(self):
        self._install_responses(
            b"<html><div>Sign in</div></html>",
            post_url="https://accounts.google.com/signin")

        result = bg_telemetry._post(
            "client-123", "1.4.4", "update", "2027", "en", "windows-x64")

        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
