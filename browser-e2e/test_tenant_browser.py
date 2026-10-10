"""Playwright real Chromium E2E. Writes sample data: use disposable Docker database only.

Required environment: SMARTBIZ_E2E_DESTRUCTIVE=YES, E2E_A_EMAIL,
E2E_A_PASSWORD, E2E_B_EMAIL, E2E_B_PASSWORD.
"""
import os
import uuid
from playwright.sync_api import expect
import pytest

pytestmark=pytest.mark.skipif(
    os.getenv('SMARTBIZ_E2E_DESTRUCTIVE')!='YES',
    reason='Explicit opt-in required: E2E writes test documents and users')
BASE=os.getenv('E2E_BASE_URL','http://127.0.0.1:8000')


def login(page,email,password):
    page.goto(BASE+'/workspace')
    page.locator('#email').fill(email)
    page.locator('#password').fill(password)
    page.locator('#login-form button').click()
    expect(page.locator('#workspace')).to_be_visible(timeout=15000)
    expect(page.locator('#identity')).to_contain_text(email)


def env(name):
    value=os.getenv(name)
    assert value, f'Missing {name}'
    return value


def test_browser_cross_tenant_no_stale_documents(page):
    login(page,env('E2E_A_EMAIL'),env('E2E_A_PASSWORD'))
    marker=uuid.uuid4().hex
    name=f'only-A-{marker[:10]}.md'
    page.locator('#file').set_input_files({
        'name':name,'mimeType':'text/markdown',
        'buffer':f'Synthetic organization A only. E2E marker: {marker}'.encode('utf-8')})
    page.locator('#upload-form button').click()
    expect(page.locator('#upload-status')).to_contain_text('已入队')
    # Same bytes must be a safe 409, never an unhandled HTTP 500.
    page.locator('#upload-form button').click()
    expect(page.locator('#upload-status')).to_contain_text('本企业已上传相同文件')
    page.locator('#refresh').click()
    expect(page.locator('#documents')).to_contain_text(name)
    page.locator('#question').fill('Previous A private query')
    page.locator('#logout').click()
    expect(page.locator('#documents')).to_be_empty()
    expect(page.locator('#question')).to_have_value('')
    expect(page.locator('#answer')).to_be_empty()
    page.locator('#email').fill(env('E2E_B_EMAIL'))
    page.locator('#password').fill(env('E2E_B_PASSWORD'))
    page.locator('#login-form button').click()
    expect(page.locator('#workspace')).to_be_visible()
    expect(page.locator('#documents')).not_to_contain_text(name)
    expect(page.locator('#question')).to_have_value('')
    expect(page.locator('#answer')).to_be_empty()
    expect(page.locator('#sources')).to_be_empty()


def test_browser_revoke_access_token_and_hide_user(page):
    admin_email=env('E2E_A_EMAIL')
    login(page,admin_email,env('E2E_A_PASSWORD'))
    staff=f'e2e-staff-{uuid.uuid4().hex[:12]}@example.com'
    password='Test-only-Example-Pass-314159!'
    page.locator('#user-email').fill(staff)
    page.locator('#user-password').fill(password)
    page.locator('#user-form button').click()
    expect(page.locator('#users')).to_contain_text(staff)
    # Request auth token directly so a real revoked credential can be asserted.
    auth=page.request.post(BASE+'/api/v2/auth/login',data={'email':staff,'password':password})
    assert auth.status==200
    secret=auth.json()['access_token']
    row=page.locator('#users .item').filter(has_text=staff)
    assert row.count()==1
    page.once('dialog',lambda dialog:dialog.accept())
    row.get_by_role('button',name='停用账号').click()
    expect(row).to_contain_text('停用')
    rejected=page.request.get(BASE+'/api/v2/auth/me',headers={'Authorization':'Bearer '+secret})
    assert rejected.status==401, 'revoked token should no longer work'
    assert page.request.post(BASE+'/api/v2/auth/login',data={'email':staff,'password':password}).status==401
