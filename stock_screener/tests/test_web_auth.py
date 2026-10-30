from unittest import mock

import pytest

from web.auth import CurrentUser, optional_user, require_user
from web.business import BusinessError
from fastapi.testclient import TestClient
from web.auth import get_db
from web.main import app


@pytest.mark.parametrize('token', [None, 'expired-session'])
def test_protected_access_requires_a_valid_session(token):
    db = mock.Mock()
    db.get_user_by_session_token.return_value = None
    with pytest.raises(BusinessError) as error:
        require_user(db=db, token=token)
    assert error.value.error_code == 'AUTH_REQUIRED'
    assert optional_user(db=db, token=token) is None


def test_valid_session_preserves_the_assigned_account():
    db = mock.Mock()
    db.get_user_by_session_token.return_value = {'id': 7, 'username': 'analyst', 'role': 'admin'}
    assert require_user(db=db, token='valid-session') == CurrentUser(7, 'analyst', 'admin')


@pytest.mark.parametrize('path', ['/api/auth/me', '/api/screening/tasks', '/api/reports'])
def test_signed_out_api_cannot_read_business_data(path):
    db = mock.Mock()
    app.dependency_overrides[get_db] = lambda: db
    try:
        with mock.patch('web.reports.ElasticsearchReportRepository.from_env') as report_store:
            # No lifespan: this test must never initialize a real database.
            response = TestClient(app).get(path)
            assert response.json()['error_code'] == 'AUTH_REQUIRED'
            report_store.assert_not_called()
    finally:
        app.dependency_overrides.pop(get_db, None)
