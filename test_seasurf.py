from __future__ import with_statement

import sys
import unittest

from flask import Flask, render_template_string, request
from flask_seasurf import SeaSurf, REASON_NO_REQUEST
from werkzeug.exceptions import Forbidden
from werkzeug.http import parse_cookie


if sys.version_info[0] < 3:
    b = lambda s: s
else:
    b = lambda s: s.encode('utf-8')


class SeaSurfTestCase(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.debug = True
        app.secret_key = '1234'
        self.app = app

        csrf = SeaSurf()
        csrf._csrf_disable = False
        self.csrf = csrf

        # Initialize CSRF protection.
        self.csrf.init_app(app)

        @csrf.exempt
        @app.route('/foo', methods=['POST'])
        @app.route('/foo/<term>', methods=['POST'])
        def foo(term=None):
            return 'bar'

        @app.route('/bar', methods=['POST'])
        @app.route('/bar/<term>', methods=['POST'])
        def bar(term=None):
            return 'foo'

    def test_generate_token(self):
        self.assertIsNotNone(self.csrf._generate_token())

    def test_unique_generation(self):
        token_a = self.csrf._generate_token()
        token_b = self.csrf._generate_token()
        self.assertNotEqual(token_a, token_b)

    def test_token_is_string(self):
        token = self.csrf._generate_token()
        self.assertEqual(type(token), str)

    def test_exempt_view(self):
        rv = self.app.test_client().post('/foo')
        self.assertIn(b('bar'), rv.data)

        rv = self.app.test_client().post(u'/foo/\xf8')
        self.assertIn(b('bar'), rv.data)

    def test_token_validation(self):
        # should produce a logger warning
        rv = self.app.test_client().post('/bar')
        self.assertIn(b('403 Forbidden'), rv.data)

        rv = self.app.test_client().post(u'/bar/\xf8')
        self.assertIn(b('403 Forbidden'), rv.data)

    def test_json_token_validation_bad(self):
        """Should fail with 403 JSON _csrf_token differers from session token"""
        tokenA = self.csrf._generate_token()
        tokenB = self.csrf._generate_token()
        data = {'_csrf_token': tokenB}
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess[self.csrf._csrf_name] = tokenA
                client.set_cookie('www.example.com', self.csrf._csrf_name, tokenB)

            rv = client.post('/bar', data=data)
            self.assertEqual(rv.status_code, 403, rv)

            rv = client.post(u'/bar/\xf8', data=data)
            self.assertEqual(rv.status_code, 403, rv)

    def test_json_token_validation_good(self):
        """Should succeed error if JSON has _csrf_token set"""
        token = self.csrf._generate_token()
        data = {'_csrf_token': token}
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                client.set_cookie('www.example.com', self.csrf._csrf_name, token)
                sess[self.csrf._csrf_name] = token

            rv = client.post('/bar', data=data)
            self.assertEqual(rv.status_code, 200, rv)

            rv = client.post(u'/bar/\xf8', data=data)
            self.assertEqual(rv.status_code, 200, rv)

    def test_https_bad_referer(self):
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                token = self.csrf._generate_token()

                client.set_cookie('www.example.com', self.csrf._csrf_name, token)
                sess[self.csrf._csrf_name] = token

            # once this is reached the session was stored
            rv = client.post('/bar',
                data={self.csrf._csrf_name: token},
                base_url='https://www.example.com',
                headers={'Referer': 'https://www.evil.com/foobar'})

            self.assertEqual(403, rv.status_code)

            rv = client.post(u'/bar/\xf8',
                data={self.csrf._csrf_name: token},
                base_url='https://www.example.com',
                headers={'Referer': u'https://www.evil.com/\xf8'})

            self.assertEqual(403, rv.status_code)

    def test_https_good_referer(self):
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                token = self.csrf._generate_token()

                client.set_cookie('www.example.com', self.csrf._csrf_name, token)
                sess[self.csrf._csrf_name] = token

            # once this is reached the session was stored
            rv = client.post('/bar',
                data={self.csrf._csrf_name: token},
                base_url='https://www.example.com',
                headers={'Referer': 'https://www.example.com/foobar'})

            self.assertEqual(rv.status_code, 200)

            rv = client.post(u'/bar/\xf8',
                data={self.csrf._csrf_name: token},
                base_url='https://www.example.com',
                headers={'Referer': 'https://www.example.com/foobar\xf8'})

            self.assertEqual(rv.status_code, 200)

    def test_token_in_header(self):
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                token = self.csrf._generate_token()
                sess[self.csrf._csrf_name] = token

            headers = {
                self.csrf._csrf_header_name: token,
            }

            rv = client.post('/bar', headers=headers)
            self.assertEqual(rv.status_code, 200, rv)

            rv = client.post(u'/bar/\xf8', headers=headers)
            self.assertEqual(rv.status_code, 200, rv)

    def test_token_in_form_data(self):
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                token = self.csrf._generate_token()
                sess[self.csrf._csrf_name] = token

            data = '{0}={1}'.format(self.csrf._csrf_name, token)
            content_type = 'application/x-www-form-urlencoded'

            rv = client.post('/bar', data=data, content_type=content_type)
            self.assertEqual(rv.status_code, 200, rv)

            rv = client.post(u'/bar/\xf8', data=data, content_type=content_type)
            self.assertEqual(rv.status_code, 200, rv)

    def test_invalid_json_does_not_return_400(self):
        """Flask with Python3 raises a BadRequest anytime someone accesses
        `request.json` with invalid JSON. Flask-Seasurf should not cause a
        400 response when checking for a csrf token.
        """
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                token = self.csrf._generate_token()
                sess[self.csrf._csrf_name] = token

            headers = {
                self.csrf._csrf_header_name: token,
            }
            data = '{]\]{'
            content_type = 'application/json'

            rv = client.post('/bar',
                             data=data,
                             content_type=content_type,
                             headers=headers)
            self.assertEqual(rv.status_code, 200, rv)

            rv = client.post(u'/bar/\xf8',
                             data=data,
                             content_type=content_type,
                             headers=headers)
            self.assertEqual(rv.status_code, 200, rv)

    def test_cannot_validate_without_request(self):
        with self.assertRaises(Forbidden) as ex:
            self.csrf.validate()
        expected_exception_message = '403 Forbidden: {0}'.format(REASON_NO_REQUEST)
        self.assertEqual(str(ex.exception), expected_exception_message)

    def assertIsNotNone(self, value):
        self.assertNotEqual(value, None)


class SeaSurfTestCaseExemptViews(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.debug = True
        app.secret_key = '1234'
        app.config['SEASURF_INCLUDE_OR_EXEMPT_VIEWS'] = 'exempt'

        self.app = app

        csrf = SeaSurf()
        csrf._csrf_disable = False
        self.csrf = csrf

        # Initialize CSRF protection.
        self.csrf.init_app(app)

        @csrf.exempt
        @app.route('/foo', methods=['POST'])
        def foo():
            return 'bar'

        @app.route('/bar', methods=['POST'])
        def bar():
            return 'foo'

    def test_exempt_view(self):
        rv = self.app.test_client().post('/foo')
        self.assertIn(b('bar'), rv.data)

    def test_token_validation(self):
        # should produce a logger warning
        rv = self.app.test_client().post('/bar')
        self.assertIn(b('403 Forbidden'), rv.data)

    def assertIn(self, value, container):
        self.assertTrue(value in container)


class SeaSurfTestCaseIncludeViews(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.debug = True
        app.secret_key = '1234'
        app.config['SEASURF_INCLUDE_OR_EXEMPT_VIEWS'] = 'include'

        self.app = app

        csrf = SeaSurf()
        csrf._csrf_disable = False
        self.csrf = csrf

        # Initialize CSRF protection.
        self.csrf.init_app(app)

        @csrf.include
        @app.route('/foo', methods=['POST'])
        @app.route('/foo/<term>', methods=['POST'])
        def foo(term=None):
            return 'bar'

        @app.route('/bar', methods=['POST'])
        @app.route('/bar/<term>', methods=['POST'])
        def bar(term=None):
            return 'foo'

    def test_include_view(self):
        rv = self.app.test_client().post('/foo')
        self.assertIn(b('403 Forbidden'), rv.data)

        rv = self.app.test_client().post(u'/foo/\xf8')
        self.assertIn(b('403 Forbidden'), rv.data)

    def test_token_validation(self):
        # should produce a logger warning
        rv = self.app.test_client().post('/bar')
        self.assertIn(b('foo'), rv.data)

        rv = self.app.test_client().post(u'/bar/\xf8')
        self.assertIn(b('foo'), rv.data)

    def assertIn(self, value, container):
        self.assertTrue(value in container)


class SeaSurfTestCaseExemptUrls(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.debug = True
        app.secret_key = '1234'

        self.app = app

        csrf = SeaSurf()
        csrf._csrf_disable = False
        self.csrf = csrf

        # Initialize CSRF protection.
        self.csrf.init_app(app)
        self.csrf.exempt_urls(('/foo',))

        @app.route('/foo/baz', methods=['POST'])
        def foobaz():
            return 'bar'

        @app.route('/foo/quz', methods=['POST'])
        def fooquz():
            return 'bar'

        @app.route('/bar', methods=['POST'])
        def bar():
            return 'foo'

    def test_exempt_view(self):
        rv = self.app.test_client().post('/foo/baz')
        self.assertIn(b('bar'), rv.data)
        rv = self.app.test_client().post('/foo/quz')
        self.assertIn(b('bar'), rv.data)

    def test_token_validation(self):
        # should produce a logger warning
        rv = self.app.test_client().post('/bar')
        self.assertIn(b('403 Forbidden'), rv.data)

    def assertIn(self, value, container):
        self.assertTrue(value in container)


class SeaSurfTestCaseDisableCookie(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.debug = True
        app.secret_key = '1234'

        self.app = app

        csrf = SeaSurf()
        csrf._csrf_disable = False
        self.csrf = csrf

        # Initialize CSRF protection.
        self.csrf.init_app(app)

        @self.csrf.disable_cookie
        def disable_cookie(response):
            if request.path == '/foo/baz':
                return True
            if request.path == '/manual':
                return True
            return False

        @app.route('/foo/baz', methods=['GET'])
        def foobaz():
            return 'bar'

        @app.route('/foo/quz', methods=['GET'])
        def fooquz():
            return 'bar'

        @csrf.exempt
        @app.route('/manual', methods=['POST'])
        def manual():
            csrf.validate()
            return 'bar'

    def test_has_csrf_cookie(self):
        with self.app.test_client() as c:
            rv = c.get('/foo/quz')
            self.assertIn(b('bar'), rv.data)
            cookie = self.getCookie(rv, self.csrf._csrf_name)
            token = self.csrf._get_token()
            self.assertEqual(cookie, token)

    def test_no_csrf_cookie(self):
        with self.app.test_client() as c:
            rv = c.get('/foo/baz')
            cookie = self.getCookie(rv, self.csrf._csrf_name)
            self.assertEqual(cookie, None)

    def test_no_csrf_cookie_even_after_manually_validated(self):
        with self.app.test_client() as c:
            rv = c.post('/manual')
            self.assertIn(b('403 Forbidden'), rv.data)
            cookie = self.getCookie(rv, self.csrf._csrf_name)
            self.assertEqual(cookie, None)

    def getCookie(self, response, cookie_name):
        cookies = response.headers.getlist('Set-Cookie')
        for cookie in cookies:
            key, value = list(parse_cookie(cookie).items())[0]
            if key == cookie_name:
                return value
        return None


class SeaSurfTestManualValidation(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.debug = True
        app.secret_key = '1234'

        self.app = app

        csrf = SeaSurf()
        csrf._csrf_disable = False
        self.csrf = csrf

        # Initialize CSRF protection.
        self.csrf.init_app(app)

        @csrf.exempt
        @app.route('/manual', methods=['POST'])
        def manual():
            csrf.validate()
            return 'bar'

    def test_can_manually_validate_exempt_views(self):
        with self.app.test_client() as c:
            rv = c.post('/manual')
            self.assertIn(b('403 Forbidden'), rv.data)
            cookie = self.getCookie(rv, self.csrf._csrf_name)
            token = self.csrf._get_token()
            self.assertEqual(cookie, token)

    def getCookie(self, response, cookie_name):
        cookies = response.headers.getlist('Set-Cookie')
        for cookie in cookies:
            key, value = list(parse_cookie(cookie).items())[0]
            if key == cookie_name:
                return value
        return None


class SeaSurfTestCaseSave(BaseTestCase):
    def setUp(self):
        app = Flask(__name__)
        app.debug = True
        app.secret_key = '1234'
        self.app = app

        @app.after_request
        def after_request(response):
            from flask import session
            response.headers['X-Session-Modified'] = str(session.modified)
            return response

        csrf = SeaSurf()
        csrf._csrf_disable = False
        self.csrf = csrf

        # Initialize CSRF protection.
        self.csrf.init_app(app)

        @app.route('/foo', methods=['GET'])
        def foo():
            return 'bar'

    def test_save(self):
        with self.app.test_client() as client:
            rv = client.get('/foo')
            self.assertIn(b('bar'), rv.data)
            self.assertEqual(rv.headers['X-Session-Modified'], 'True')

            rv = client.get('/foo')
            self.assertIn(b('bar'), rv.data)
            self.assertEqual(rv.headers['X-Session-Modified'], 'False')

    def assertIn(self, value, container):
        self.assertTrue(value in container)

class SeaSurfTestCaseReferer(BaseTestCase):
    def setUp(self):
        app = Flask(__name__)
        app.debug = True
        app.secret_key = '1234'
        app.config['CSRF_CHECK_REFERER'] = False
        self.app = app

        csrf = SeaSurf()
        csrf._csrf_disable = False
        self.csrf = csrf

        # Initialize CSRF protection.
        self.csrf.init_app(app)

        @csrf.exempt
        @app.route('/foo', methods=['POST'])
        @app.route('/foo/<term>', methods=['POST'])
        def foo(term=None):
            return 'bar'

        @app.route('/bar', methods=['POST'])
        @app.route('/bar/<term>', methods=['POST'])
        def bar(term=None):
            return 'foo'

    def test_https_referer_check_disabled(self):
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                token = self.csrf._generate_token()

                client.set_cookie('www.example.com', self.csrf._csrf_name, token)
                sess[self.csrf._csrf_name] = token

            # once this is reached the session was stored
            rv = client.post('/bar',
                data={self.csrf._csrf_name: token},
                base_url='https://www.example.com',
                headers={'Referer': 'https://www.evil.com/foobar'})

            self.assertEqual(200, rv.status_code)

            rv = client.post(u'/bar/\xf8',
                data={self.csrf._csrf_name: token},
                base_url='https://www.example.com',
                headers={'Referer': u'https://www.evil.com/\xf8'})

            self.assertEqual(200, rv.status_code)


def suite():
    suite = unittest.TestSuite()
    suite.addTest(unittest.makeSuite(SeaSurfTestCase))
    suite.addTest(unittest.makeSuite(SeaSurfTestCaseExemptViews))
    suite.addTest(unittest.makeSuite(SeaSurfTestCaseIncludeViews))
    suite.addTest(unittest.makeSuite(SeaSurfTestCaseExemptUrls))
    suite.addTest(unittest.makeSuite(SeaSurfTestCaseSave))
    suite.addTest(unittest.makeSuite(SeaSurfTestCaseSetCookie))
    suite.addTest(unittest.makeSuite(SeaSurfTestCaseReferer))
    suite.addTest(unittest.makeSuite(SeaSurfTestManualValidation))
    return suite


if __name__ == '__main__':
    unittest.main(defaultTest='suite')
