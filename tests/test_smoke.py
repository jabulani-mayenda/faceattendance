from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
import pytest
from app.main import app
from app.db import Base, engine

client = TestClient(app)

def test_health():
    r=client.get('/api/health')
    assert r.status_code == 200
    assert r.json()['status'] == 'ok'
    from app.recognition import model_status
    expected = 'ready' if model_status()['configured'] else 'not_configured'
    assert r.json()['recognition'] == expected

def test_login_page():
    r=client.get('/login')
    assert r.status_code == 200
    assert 'FaceTrack' in r.text

def test_auth_required():
    r=client.get('/api/people')
    assert r.status_code == 401

def test_sqlite_foreign_keys_are_enabled():
    if engine.dialect.name != 'sqlite':
        return
    with engine.connect() as connection:
        assert connection.scalar(text('PRAGMA foreign_keys')) == 1

def test_admin_bootstrap_rejects_missing_password(monkeypatch):
    from app.main import seed_admin
    monkeypatch.delenv('FACETRACK_ADMIN_PASSWORD', raising=False)
    test_engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(test_engine)
    with Session(test_engine) as db:
        with pytest.raises(RuntimeError, match='FACETRACK_ADMIN_PASSWORD'):
            seed_admin(db)
    test_engine.dispose()

def test_encrypted_face_template_round_trip(monkeypatch):
    import numpy as np
    from cryptography.fernet import Fernet
    from app.recognition import decrypt_embedding, encrypt_embedding
    monkeypatch.setenv('FACETRACK_BIOMETRIC_KEY', Fernet.generate_key().decode())
    vector=np.arange(128,dtype=np.float32)
    protected=encrypt_embedding(vector)
    assert vector.tobytes() not in protected
    assert np.array_equal(vector,decrypt_embedding(protected))

def test_camera_match_rejects_frame_without_a_face(monkeypatch):
    import cv2
    import numpy as np
    from cryptography.fernet import Fernet
    from app.recognition import model_status
    monkeypatch.setenv('FACETRACK_ADMIN_PASSWORD','Test-Only-Strong-Password-2026!')
    monkeypatch.setenv('FACETRACK_BIOMETRIC_KEY',Fernet.generate_key().decode())
    if not model_status()['configured']:
        pytest.skip('Optional recognition models have not been downloaded')
    ok,encoded=cv2.imencode('.jpg',np.zeros((240,320,3),dtype=np.uint8))
    assert ok
    with TestClient(app) as c:
        login=c.post('/api/login',data={'username':'admin','password':'Test-Only-Strong-Password-2026!'})
        assert login.status_code==200
        response=c.post('/api/recognition/match',headers={'X-CSRF-Token':c.cookies.get('ft_csrf')},files={'image':('blank.jpg',encoded.tobytes(),'image/jpeg')})
        assert response.status_code==422
        assert 'No face detected' in response.json()['detail']

def test_school_teacher_and_hod_scopes(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from uuid import uuid4
    password='Test-Only-Strong-Password-2026!'
    monkeypatch.setenv('FACETRACK_ADMIN_PASSWORD',password)
    suffix=uuid4().hex[:8]
    now=datetime.now(timezone.utc)
    with TestClient(app) as admin:
        assert admin.post('/api/login',data={'username':'admin','password':password}).status_code==200
        admin_headers={'X-CSRF-Token':admin.cookies.get('ft_csrf')}
        hod=admin.post('/api/users',headers=admin_headers,json={'username':'hod-'+suffix,'password':password,'role':'academic_admin','department':'Science'})
        arts=admin.post('/api/users',headers=admin_headers,json={'username':'arts-'+suffix,'password':password,'role':'lecturer','department':'Arts'})
        assert hod.status_code==arts.status_code==200
        science_group='Science-'+suffix; arts_group='Arts-'+suffix
        science_person=admin.post('/api/people',headers=admin_headers,json={'person_code':'S-'+suffix,'first_name':'Science','last_name':'Student','mode':'education','group_name':science_group})
        arts_person=admin.post('/api/people',headers=admin_headers,json={'person_code':'A-'+suffix,'first_name':'Arts','last_name':'Student','mode':'education','group_name':arts_group})
        misplaced=admin.post('/api/people',headers=admin_headers,json={'person_code':'M-'+suffix,'first_name':'Other','last_name':'Group','mode':'education','group_name':'Computer Science'})
        assert science_person.status_code==arts_person.status_code==misplaced.status_code==200
        arts_session=admin.post('/api/sessions',headers=admin_headers,json={'name':'Arts class','group_name':arts_group,'teacher_id':arts.json()['id'],'starts_at':now.isoformat(),'ends_at':(now+timedelta(hours=1)).isoformat()})
        assert arts_session.status_code==200

    with TestClient(app) as head:
        assert head.post('/api/login',data={'username':'hod-'+suffix,'password':password}).status_code==200
        head_headers={'X-CSRF-Token':head.cookies.get('ft_csrf')}
        science=head.post('/api/users',headers=head_headers,json={'username':'science-'+suffix,'password':password,'role':'lecturer','department':'Science'})
        assert science.status_code==200
        owned_teachers=head.get('/api/users?role=lecturer').json()
        assert {item['id'] for item in owned_teachers}=={science.json()['id']}
        own_session=head.post('/api/sessions',headers=head_headers,json={'name':'Science class','group_name':science_group,'teacher_id':science.json()['id'],'starts_at':now.isoformat(),'ends_at':(now+timedelta(hours=1)).isoformat()})
        assert own_session.status_code==200
        assert head.post('/api/sessions',headers=head_headers,json={'name':'Wrong department','group_name':arts_group,'teacher_id':arts.json()['id'],'starts_at':now.isoformat(),'ends_at':(now+timedelta(hours=1)).isoformat()}).status_code==403

    with TestClient(app) as teacher:
        assert teacher.post('/api/login',data={'username':'science-'+suffix,'password':password}).status_code==200
        sessions=teacher.get('/api/sessions').json()
        assert len(sessions)==1 and sessions[0]['id']==own_session.json()['id']
        roster={person['code'] for person in teacher.get('/api/people').json()}
        assert 'S-'+suffix in roster and 'A-'+suffix not in roster
        teacher_headers={'X-CSRF-Token':teacher.cookies.get('ft_csrf')}
        payload={'person_code':'S-'+suffix,'mode':'education','session_id':own_session.json()['id'],'identity_verified':True}
        recorded=teacher.post('/api/attendance',headers=teacher_headers,json=payload)
        assert recorded.status_code==200
        assert len(teacher.get('/api/attendance').json())==1
        other_class={'person_code':'A-'+suffix,'mode':'education','session_id':arts_session.json()['id'],'identity_verified':True}
        assert teacher.post('/api/attendance',headers=teacher_headers,json=other_class).status_code==403
        group_mismatch={'person_code':'M-'+suffix,'mode':'education','session_id':own_session.json()['id'],'identity_verified':True}
        mismatch=teacher.post('/api/attendance',headers=teacher_headers,json=group_mismatch)
        assert mismatch.status_code==400
        assert 'Computer Science' in mismatch.json()['detail'] and science_group in mismatch.json()['detail']

    with TestClient(app) as head:
        assert head.post('/api/login',data={'username':'hod-'+suffix,'password':password}).status_code==200
        assert {item['person'] for item in head.get('/api/attendance').json()}=={'Science Student'}

def test_enrollment_dialog_has_explicit_gated_save():
    from pathlib import Path
    source=(Path(__file__).resolve().parents[1]/'static'/'app.js').read_text(encoding='utf-8')
    assert 'Save enrollment' in source
    assert 'samples.length<3||!$(\'#enrollConsent\').checked' in source
    edit_ui=(Path(__file__).resolve().parents[1]/'static'/'person-edit.js').read_text(encoding='utf-8')
    assert 'does not remove the saved face template' in edit_ui

def test_admin_workflow_and_immutable_correction(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from uuid import uuid4
    password = 'Test-Only-Strong-Password-2026!'
    monkeypatch.setenv('FACETRACK_ADMIN_PASSWORD', password)
    with TestClient(app) as c:
        login = c.post('/api/login', data={'username':'admin','password':password})
        assert login.status_code == 200
        headers = {'X-CSRF-Token': c.cookies.get('ft_csrf')}
        denied = c.post('/api/users', json={'username':'no-csrf','password':password,'role':'auditor'})
        assert denied.status_code == 403
        now = datetime.now(timezone.utc)
        invalid_session = c.post('/api/sessions', headers=headers, json={'name':'Invalid grace','starts_at':now.isoformat(),'ends_at':(now+timedelta(hours=1)).isoformat(),'grace_minutes':-1})
        assert invalid_session.status_code == 422
        invalid_shift = c.post('/api/shifts', headers=headers, json={'name':'Invalid grace','starts_at':now.isoformat(),'ends_at':(now+timedelta(hours=1)).isoformat(),'grace_minutes':-1})
        assert invalid_shift.status_code == 422
        code = 'T-' + uuid4().hex[:10]
        group='Class-'+code
        teacher=c.post('/api/users',headers=headers,json={'username':'teacher-'+uuid4().hex[:8],'password':password,'role':'lecturer','department':'Science'})
        assert teacher.status_code==200
        person = c.post('/api/people', headers=headers, json={'person_code':code,'first_name':'Demo','last_name':'Person','mode':'education','group_name':group})
        assert person.status_code == 200
        session = c.post('/api/sessions', headers=headers, json={'name':'Test '+code,'group_name':group,'teacher_id':teacher.json()['id'],'starts_at':now.isoformat(),'ends_at':(now+timedelta(hours=1)).isoformat(),'grace_minutes':10})
        assert session.status_code == 200
        event_uid = str(uuid4())
        attendance_data = {'person_code':code,'mode':'education','session_id':session.json()['id'],'event_uid':event_uid,'identity_verified':True}
        event = c.post('/api/attendance', headers=headers, json=attendance_data)
        assert event.status_code == 200
        duplicate_retry = c.post('/api/attendance', headers=headers, json=attendance_data)
        assert duplicate_retry.status_code == 200
        assert duplicate_retry.json()['duplicate_sync'] is True
        duplicate = c.post('/api/attendance', headers=headers, json={'person_code':code,'mode':'education','session_id':session.json()['id']})
        assert duplicate.status_code == 409
        correction = c.post(f"/api/attendance/{event.json()['event']['id']}/correction", headers=headers, data={'reason':'Verified by authorized operator','new_type':'late'})
        assert correction.status_code == 200
        assert correction.json()['source_event_unchanged'] is True
        listed = c.get('/api/attendance')
        assert listed.status_code == 200
        assert next(row for row in listed.json() if row['id']==event.json()['event']['id'])['type'] == 'late'
        report = c.get('/api/reports/attendance.csv')
        assert report.status_code == 200
        assert 'late' in report.text

        workplace_code='W-'+uuid4().hex[:10]
        assert c.post('/api/people',headers=headers,json={'person_code':workplace_code,'first_name':'Shift','last_name':'Person','mode':'workplace'}).status_code==200
        shift=c.post('/api/shifts',headers=headers,json={'name':'Test shift '+workplace_code,'starts_at':(now-timedelta(minutes=1)).isoformat(),'ends_at':(now+timedelta(hours=4)).isoformat(),'grace_minutes':5})
        assert shift.status_code==200
        checkout_first=c.post('/api/attendance',headers=headers,json={'person_code':workplace_code,'mode':'workplace','shift_id':shift.json()['id'],'event_type':'check_out','identity_verified':True})
        assert checkout_first.status_code==409
        checkin=c.post('/api/attendance',headers=headers,json={'person_code':workplace_code,'mode':'workplace','shift_id':shift.json()['id'],'event_type':'check_in','identity_verified':True})
        assert checkin.status_code==200
        checkin_retry=c.post('/api/attendance',headers=headers,json={'person_code':workplace_code,'mode':'workplace','shift_id':shift.json()['id'],'event_type':'check_in','identity_verified':True})
        assert checkin_retry.status_code==409
        checkout=c.post('/api/attendance',headers=headers,json={'person_code':workplace_code,'mode':'workplace','shift_id':shift.json()['id'],'event_type':'check_out','occurred_at':(now+timedelta(minutes=1)).isoformat(),'identity_verified':True})
        assert checkout.status_code==200
        assert checkout.json()['event']['type']=='check_out'
        assert c.post('/api/logout',headers=headers).status_code==200
        assert c.get('/api/me').status_code==401
