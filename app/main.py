from datetime import datetime, timedelta, timezone, date
from uuid import uuid4
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import numpy as np
import csv, io, os
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, Request, Form, File, UploadFile, HTTPException, Response
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session
from sqlalchemy import select, func, desc
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel, Field
ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT/'.env')
from .db import DATABASE_URL, get_db, SessionLocal
from .models import (User, UserSession, Person, Session as ClassSession, Shift, AttendanceEvent,
                     AuditLog, Correction, SyncReceipt, BiometricTemplate, RecognitionLog)
from .security import (hash_password, verify_password, create_session, current_user, current_session,
                       require_user, require_roles, login_locked, record_login_failure, clear_login_failures)
from .recognition import (extract_embedding, encrypt_embedding, decrypt_embedding,
                          match_threshold, model_status)

def migrate_database():
    config=Config(str(ROOT/'alembic.ini'))
    config.set_main_option('script_location',str(ROOT/'migrations'))
    config.set_main_option('sqlalchemy.url',DATABASE_URL.replace('%','%%'))
    command.upgrade(config,'head')

migrate_database()
app = FastAPI(title='FaceTrack', version='0.1.0', description='Offline-first attendance platform')
app.mount('/static', StaticFiles(directory=ROOT/'static'), name='static')
templates = Jinja2Templates(directory=ROOT/'templates')


def as_utc(value):
    if value is None: return value
    if value.tzinfo is None: return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)

def app_timezone():
    try: return ZoneInfo(os.getenv('FACETRACK_TIMEZONE','UTC'))
    except ZoneInfoNotFoundError: return timezone.utc

def audit(db, actor, action, target, detail=None):
    db.add(AuditLog(actor=actor, action=action, target=target, detail=detail))
    db.commit()

def seed_admin(db):
    if not db.scalar(select(User).where(User.username == 'admin')):
        password = os.getenv('FACETRACK_ADMIN_PASSWORD', '')
        if len(password) < 12:
            raise RuntimeError('Set FACETRACK_ADMIN_PASSWORD to at least 12 characters before first startup')
        db.add(User(username='admin', password_hash=hash_password(password), role='super_admin'))
        db.commit()

@app.on_event('startup')
def startup():
    db = SessionLocal()
    try: seed_admin(db)
    finally: db.close()

@app.get('/', response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user: return RedirectResponse('/login', status_code=303)
    return templates.TemplateResponse(request, 'index.html', {'username': user.username, 'role': user.role,'department':user.department})

@app.get('/login', response_class=HTMLResponse)
def login_page(request: Request): return templates.TemplateResponse(request, 'login.html', {})

@app.post('/api/login')
def login(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    client_ip = request.client.host if request.client else 'unknown'
    login_key = f'{client_ip}:{username.casefold()}'
    if login_locked(login_key):
        raise HTTPException(429, 'Too many failed sign-in attempts. Try again in 15 minutes.')
    user = db.scalar(select(User).where(User.username == username, User.active == True))
    if not user or not verify_password(password, user.password_hash):
        record_login_failure(login_key)
        raise HTTPException(401, 'Incorrect username or password')
    clear_login_failures(login_key)
    token, csrf_token = create_session(user, db)
    audit(db, user.username, 'login', f'user:{user.id}')
    response = JSONResponse({'ok': True, 'username': user.username, 'role': user.role})
    secure = os.getenv('COOKIE_SECURE','0') == '1'
    response.set_cookie('ft_session', token, httponly=True, samesite='strict', secure=secure, max_age=36000)
    response.set_cookie('ft_csrf', csrf_token, httponly=False, samesite='strict', secure=secure, max_age=36000)
    return response

@app.post('/api/logout')
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    user = require_user(request, db)
    session = current_session(request, db)
    if session:
        session.revoked_at = datetime.now(timezone.utc)
        db.commit()
    audit(db, user.username, 'logout', f'user:{user.id}')
    response = JSONResponse({'ok': True})
    response.delete_cookie('ft_session'); response.delete_cookie('ft_csrf')
    return response

@app.get('/api/me')
def me(request: Request, db: Session = Depends(get_db)):
    u = require_user(request, db); return {'username':u.username, 'role':u.role}

@app.get('/api/dashboard')
def dashboard(request: Request, db: Session = Depends(get_db)):
    u = require_user(request, db)
    local_today = datetime.now(app_timezone()).date()
    start = datetime.combine(local_today, datetime.min.time(), tzinfo=app_timezone()).astimezone(timezone.utc)
    end = datetime.combine(local_today + timedelta(days=1), datetime.min.time(), tzinfo=app_timezone()).astimezone(timezone.utc)
    events = db.scalars(scope_attendance(select(AttendanceEvent).where(AttendanceEvent.occurred_at >= start, AttendanceEvent.occurred_at < end),u)).all()
    present_ids = {e.person_id for e in events if e.event_type in ('check_in','present','late')}
    late_ids = {e.person_id for e in events if e.event_type == 'late'}
    overrides = correction_map(db, events)
    present_ids = {e.person_id for e in events if overrides.get(e.id, e.event_type) in ('check_in','present','late')}
    late_ids = {e.person_id for e in events if overrides.get(e.id, e.event_type) == 'late'}
    recent = db.scalars(scope_attendance(select(AttendanceEvent).order_by(desc(AttendanceEvent.occurred_at)),u).limit(8)).all()
    recent_overrides=correction_map(db,recent)
    people_query=select(func.count(Person.id))
    if u.role=='lecturer' or (u.role=='academic_admin' and u.department):
        people_query=people_query.where(func.lower(func.trim(Person.group_name)).in_(assigned_group_names(u)))
    pending=scope_attendance(select(func.count(AttendanceEvent.id)).where(AttendanceEvent.sync_status!='synced'),u)
    sessions = db.scalars(select(ClassSession).where(ClassSession.id.in_(assigned_session_ids(u))).order_by(desc(ClassSession.starts_at))).all() if u.role in ('lecturer', 'academic_admin') else []
    session_rows = []
    for session in sessions:
        session_events = [event for event in events if event.session_id == session.id]
        session_rows.append({'id': session.id, 'name': session.name, 'group': session.group_name,
                             'teacher': session.teacher.username if session.teacher else 'Unassigned',
                             'starts_at': session.starts_at.isoformat(), 'ends_at': session.ends_at.isoformat(),
                             'active': session.active, 'events_today': len(session_events),
                             'present_today': len({e.person_id for e in session_events if overrides.get(e.id, e.event_type) in ('check_in','present','late')}),
                             'late_today': len({e.person_id for e in session_events if overrides.get(e.id, e.event_type) == 'late'})})
    department_teachers = []
    if u.role == 'academic_admin':
        teachers = db.scalars(select(User).where(User.role == 'lecturer', User.department == u.department, User.active == True).order_by(User.username)).all()
        for teacher in teachers:
            teacher_events = [event for event in events if event.session and event.session.teacher_id == teacher.id]
            teacher_sessions = [session for session in session_rows if session['teacher'] == teacher.username]
            department_teachers.append({'username': teacher.username, 'sessions': len(teacher_sessions),
                                        'events_today': len(teacher_events),
                                        'present_today': len({e.person_id for e in teacher_events if overrides.get(e.id, e.event_type) in ('check_in','present','late')}),
                                        'late_today': len({e.person_id for e in teacher_events if overrides.get(e.id, e.event_type) == 'late'})})
    return {'role':u.role,'department':u.department,'people':db.scalar(people_query) or 0,'events_today':len(events),'present':len(present_ids),'late':len(late_ids),'pending_sync':db.scalar(pending) or 0,'recent':[event_dict(e, recent_overrides.get(e.id)) for e in recent], 'sessions':session_rows, 'teachers':department_teachers}

def correction_map(db, events):
    ids = [e.id for e in events]
    if not ids:
        return {}
    rows = db.scalars(select(Correction).where(Correction.event_id.in_(ids)).order_by(Correction.created_at, Correction.id)).all()
    latest = {}
    for row in rows:
        latest[row.event_id] = row.after_value
    return latest

def event_dict(e, resolved_type=None):
    return {'id':e.id,'uid':e.event_uid,'person_id':e.person_id,'person':e.person.full_name if e.person else 'Unknown','code':e.person.person_code if e.person else '', 'mode':e.mode,'type':resolved_type or e.event_type,'source_type':e.event_type,'corrected':resolved_type is not None and resolved_type != e.event_type,'occurred_at':e.occurred_at.isoformat(),'source':e.source,'sync_status':e.sync_status,'note':e.note}

def assigned_session_ids(user: User):
    if user.role == 'lecturer':
        return select(ClassSession.id).where(ClassSession.teacher_id == user.id)
    if user.role == 'academic_admin' and user.department:
        return (select(ClassSession.id).join(User, ClassSession.teacher_id == User.id)
                .where(User.department == user.department, User.role == 'lecturer'))
    return select(ClassSession.id)

def assigned_group_names(user: User):
    stmt=select(func.lower(func.trim(ClassSession.group_name))).where(ClassSession.group_name.is_not(None))
    if user.role=='lecturer':
        return stmt.where(ClassSession.teacher_id==user.id)
    if user.role=='academic_admin' and user.department:
        return stmt.join(User,ClassSession.teacher_id==User.id).where(User.department==user.department,User.role=='lecturer')
    return stmt

def scope_attendance(stmt, user: User):
    if user.role == 'lecturer' or (user.role == 'academic_admin' and user.department):
        return stmt.where(AttendanceEvent.session_id.in_(assigned_session_ids(user)))
    return stmt

def person_dict(p, biometric_enrolled=False):
    return {'id':p.id,'code':p.person_code,'first_name':p.first_name,'last_name':p.last_name,'name':p.full_name,'email':p.email,'department':p.department,'group':p.group_name,'mode':p.mode,'status':p.status,'biometric_enrolled':biometric_enrolled}

@app.get('/api/people')
def people(request: Request, q: str = '', db: Session = Depends(get_db)):
    u=require_user(request, db)
    stmt = select(Person).order_by(Person.last_name, Person.first_name)
    if u.role == 'lecturer':
        stmt=stmt.where(func.lower(func.trim(Person.group_name)).in_(assigned_group_names(u)))
    elif u.role=='academic_admin' and u.department:
        stmt=stmt.where(func.lower(func.trim(Person.group_name)).in_(assigned_group_names(u)))
    if q: stmt = stmt.where((Person.person_code.ilike(f'%{q}%')) | (Person.first_name.ilike(f'%{q}%')) | (Person.last_name.ilike(f'%{q}%')))
    rows=db.scalars(stmt.limit(500)).all()
    enrolled=set(db.scalars(select(BiometricTemplate.person_id).where(BiometricTemplate.person_id.in_([p.id for p in rows]))).all()) if rows else set()
    return [person_dict(p,p.id in enrolled) for p in rows]

class PersonIn(BaseModel):
    person_code: str = Field(min_length=1, max_length=80)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: str | None = None
    department: str | None = Field(default=None,max_length=120)
    group_name: str | None = None
    mode: str = 'education'

@app.post('/api/people')
def add_person(data: PersonIn, request: Request, db: Session = Depends(get_db)):
    u=require_user(request,db); require_roles(u,'super_admin','org_admin','hr_manager','academic_admin')
    if data.mode not in ('education','workplace'): raise HTTPException(400,'Invalid mode')
    if db.scalar(select(Person).where(Person.person_code==data.person_code)): raise HTTPException(409,'Person code already exists')
    p=Person(**data.model_dump()); db.add(p); db.commit(); db.refresh(p); audit(db,u.username,'person_created',f'person:{p.id}',p.person_code); return person_dict(p)

@app.patch('/api/people/{person_id}')
def update_person(person_id:int, data:PersonIn, request:Request, db:Session=Depends(get_db)):
    u=require_user(request,db); require_roles(u,'super_admin','org_admin','hr_manager','academic_admin')
    p=db.get(Person,person_id)
    if not p: raise HTTPException(404,'Person not found')
    for k,v in data.model_dump().items(): setattr(p,k,v)
    db.commit(); audit(db,u.username,'person_updated',f'person:{p.id}',p.person_code); return person_dict(p)

@app.post('/api/people/{person_id}/deactivate')
def deactivate_person(person_id:int,request:Request,db:Session=Depends(get_db)):
    u=require_user(request,db); require_roles(u,'super_admin','org_admin','hr_manager','academic_admin')
    p=db.get(Person,person_id)
    if not p: raise HTTPException(404,'Person not found')
    p.status='inactive'; db.commit(); audit(db,u.username,'person_deactivated',f'person:{p.id}'); return {'ok':True}

@app.post('/api/people/{person_id}/activate')
def activate_person(person_id:int,request:Request,db:Session=Depends(get_db)):
    u=require_user(request,db); require_roles(u,'super_admin','org_admin','hr_manager','academic_admin')
    p=db.get(Person,person_id)
    if not p: raise HTTPException(404,'Person not found')
    p.status='active'; db.commit(); audit(db,u.username,'person_activated',f'person:{p.id}'); return {'ok':True}

@app.get('/api/sessions')
def sessions(request:Request,db:Session=Depends(get_db)):
    u=require_user(request,db)
    stmt=select(ClassSession).order_by(desc(ClassSession.starts_at))
    if u.role=='lecturer' or (u.role=='academic_admin' and u.department):
        stmt=stmt.where(ClassSession.id.in_(assigned_session_ids(u)))
    return [{'id':s.id,'name':s.name,'group':s.group_name,'teacher':s.teacher.username if s.teacher else None,'starts_at':s.starts_at.isoformat(),'ends_at':s.ends_at.isoformat(),'grace_minutes':s.grace_minutes,'active':s.active} for s in db.scalars(stmt).all()]

class SessionIn(BaseModel): name:str; group_name:str|None=None; teacher_id:int|None=None; starts_at:datetime; ends_at:datetime; grace_minutes:int=Field(default=10, ge=0, le=180)
@app.post('/api/sessions')
def add_session(data:SessionIn,request:Request,db:Session=Depends(get_db)):
    u=require_user(request,db); require_roles(u,'super_admin','org_admin','academic_admin')
    if data.ends_at <= data.starts_at: raise HTTPException(400,'End time must be after start time')
    if not data.teacher_id: raise HTTPException(400,'Assign this class session to a teacher')
    if not data.group_name: raise HTTPException(400,'Set the class/group so the teacher can only access their students')
    teacher=db.get(User,data.teacher_id)
    if not teacher or not teacher.active or teacher.role!='lecturer': raise HTTPException(400,'Choose an active teacher account')
    if u.role=='academic_admin' and u.department and teacher.department!=u.department: raise HTTPException(403,'Teachers must belong to your department')
    s=ClassSession(**data.model_dump()); db.add(s); db.commit(); db.refresh(s); audit(db,u.username,'session_created',f'session:{s.id}'); return {'id':s.id}

class ActiveIn(BaseModel): active:bool

@app.patch('/api/sessions/{session_id}')
def update_session(session_id:int,data:ActiveIn,request:Request,db:Session=Depends(get_db)):
    u=require_user(request,db); require_roles(u,'super_admin','org_admin','academic_admin')
    session=db.get(ClassSession,session_id)
    if not session: raise HTTPException(404,'Session not found')
    if u.role=='academic_admin' and u.department and session.id not in db.scalars(assigned_session_ids(u)).all(): raise HTTPException(403,'This session belongs to another department')
    session.active=data.active; db.commit(); audit(db,u.username,'session_status_changed',f'session:{session.id}',str(data.active))
    return {'id':session.id,'active':session.active}

@app.get('/api/shifts')
def shifts(request:Request,db:Session=Depends(get_db)):
    require_user(request,db)
    return [{'id':s.id,'name':s.name,'starts_at':s.starts_at.isoformat(),'ends_at':s.ends_at.isoformat(),'grace_minutes':s.grace_minutes,'active':s.active} for s in db.scalars(select(Shift).order_by(desc(Shift.starts_at))).all()]

class ShiftIn(BaseModel): name:str; starts_at:datetime; ends_at:datetime; grace_minutes:int=Field(default=5, ge=0, le=180)
@app.post('/api/shifts')
def add_shift(data:ShiftIn,request:Request,db:Session=Depends(get_db)):
    u=require_user(request,db); require_roles(u,'super_admin','org_admin','hr_manager')
    if data.ends_at <= data.starts_at: raise HTTPException(400,'End time must be after start time')
    s=Shift(**data.model_dump()); db.add(s); db.commit(); db.refresh(s); audit(db,u.username,'shift_created',f'shift:{s.id}'); return {'id':s.id}

@app.patch('/api/shifts/{shift_id}')
def update_shift(shift_id:int,data:ActiveIn,request:Request,db:Session=Depends(get_db)):
    u=require_user(request,db); require_roles(u,'super_admin','org_admin','hr_manager')
    shift=db.get(Shift,shift_id)
    if not shift: raise HTTPException(404,'Shift not found')
    shift.active=data.active; db.commit(); audit(db,u.username,'shift_status_changed',f'shift:{shift.id}',str(data.active))
    return {'id':shift.id,'active':shift.active}

class EventIn(BaseModel):
    person_code:str
    mode:str
    event_type:str|None=None
    session_id:int|None=None
    shift_id:int|None=None
    source:str='manual'
    device_id:str|None=None
    occurred_at:datetime|None=None
    note:str|None=Field(default=None, max_length=2000)
    event_uid:str|None=Field(default=None, min_length=1, max_length=64)
    identity_verified:bool=False

@app.post('/api/attendance')
def record_attendance(data:EventIn,request:Request,db:Session=Depends(get_db)):
    u=require_user(request,db)
    if u.role=='auditor': raise HTTPException(403,'Auditor accounts cannot record attendance')
    if u.role=='hr_manager' and data.mode!='workplace': raise HTTPException(403,'HR accounts can record workplace attendance only')
    if u.role=='lecturer' and data.mode!='education': raise HTTPException(403,'Teacher accounts can record class attendance only')
    if u.role=='academic_admin' and data.mode!='education': raise HTTPException(403,'Department head accounts can manage class attendance only')
    # Check the client event ID before applying attendance state transitions. A retry
    # must stay idempotent even after the first request changed that state.
    uid=data.event_uid or str(uuid4())
    if data.event_uid and db.scalar(select(AttendanceEvent).where(AttendanceEvent.event_uid==data.event_uid)):
        return {'ok':True,'duplicate_sync':True}
    if data.source != 'manual':
        raise HTTPException(400,'Client-submitted attendance must use the manual source')
    if data.occurred_at is not None and u.role != 'super_admin':
        raise HTTPException(403,'Only a super administrator may enter a historical timestamp')
    p=db.scalar(select(Person).where(Person.person_code==data.person_code,Person.status=='active'))
    if not p: raise HTTPException(404,'Active person not found')
    if data.mode != p.mode: raise HTTPException(400,'Attendance mode does not match person policy')
    when=data.occurred_at or datetime.now(timezone.utc)
    et=data.event_type
    if data.mode=='education':
        if not data.session_id: raise HTTPException(400,'Select the scheduled class session')
        s=db.get(ClassSession,data.session_id)
        if not s or not s.active: raise HTTPException(404,'Active session not found')
        if u.role=='academic_admin' and u.department and s.id not in db.scalars(assigned_session_ids(u)).all(): raise HTTPException(403,'This class is outside your department')
        if s.group_name and (not p.group_name or p.group_name.strip().casefold()!=s.group_name.strip().casefold()):
            actual=p.group_name or 'not assigned to a class/group'
            raise HTTPException(400,f'Person is in "{actual}" but this session is for "{s.group_name}". Edit the student profile or choose a matching session.')
        if u.role=='lecturer' and s.teacher_id!=u.id: raise HTTPException(403,'This class session is assigned to another teacher')
        session_start, session_end = as_utc(s.starts_at), as_utc(s.ends_at)
        if when < session_start or when > session_end: raise HTTPException(400,'Attendance can only be recorded while the session is in progress')
        existing=db.scalar(select(AttendanceEvent).where(AttendanceEvent.person_id==p.id,AttendanceEvent.session_id==s.id,AttendanceEvent.event_type.in_(['present','late'])))
        if existing: raise HTTPException(409,'Attendance already recorded for this session')
        et='late' if when > session_start + timedelta(minutes=s.grace_minutes) else 'present'
        shift_id=None
    elif data.mode=='workplace':
        if not data.shift_id: raise HTTPException(400,'Select the assigned shift')
        sh=db.get(Shift,data.shift_id)
        if not sh or not sh.active: raise HTTPException(404,'Active shift not found')
        intent=data.event_type
        if intent not in ('check_in','check_out'): raise HTTPException(400,'Choose check-in or check-out explicitly')
        shift_start, shift_end = as_utc(sh.starts_at), as_utc(sh.ends_at)
        prior_events=db.scalars(select(AttendanceEvent).where(AttendanceEvent.person_id==p.id,AttendanceEvent.shift_id==sh.id).order_by(AttendanceEvent.occurred_at,AttendanceEvent.id)).all()
        checkins=[e for e in prior_events if e.event_type in ('check_in','late')]
        checkouts=[e for e in prior_events if e.event_type=='check_out']
        if intent=='check_in':
            if checkins: raise HTTPException(409,'A check-in already exists for this shift')
            if when < shift_start or when > shift_end: raise HTTPException(400,'Check-in can only be recorded while the shift is in progress')
            et='late' if when > shift_start + timedelta(minutes=sh.grace_minutes) else 'check_in'
        else:
            if not checkins: raise HTTPException(409,'Check-in is required before check-out')
            if checkouts: raise HTTPException(409,'This shift already has a check-out')
            last_checkin=checkins[-1]
            if when <= as_utc(last_checkin.occurred_at): raise HTTPException(400,'Check-out must follow check-in')
            if when > shift_end + timedelta(hours=12): raise HTTPException(400,'Check-out window has ended')
            et='check_out'
        session_id=None
    else: raise HTTPException(400,'Invalid attendance mode')
    if not data.identity_verified:
        raise HTTPException(400,'Confirm that you verified the person in front of you before recording attendance')
    ev=AttendanceEvent(event_uid=uid,person_id=p.id,mode=data.mode,event_type=et,occurred_at=when,source=data.source,device_id=data.device_id,session_id=data.session_id if data.mode=='education' else None,shift_id=data.shift_id if data.mode=='workplace' else None,sync_status='local',note=data.note)
    db.add(ev)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # Unique indexes are the final guard if two operators submit the same
        # person/shift at once. Convert that race into a useful conflict.
        if data.event_uid and db.scalar(select(AttendanceEvent).where(AttendanceEvent.event_uid == data.event_uid)):
            return {'ok':True,'duplicate_sync':True}
        raise HTTPException(409, 'Attendance for this person and schedule was already recorded')
    db.refresh(ev)
    audit(db,u.username,'attendance_recorded',f'event:{ev.id}',f'{p.person_code}:{et}')
    return {'ok':True,'event':event_dict(ev),'message':'Attendance saved locally'}

@app.get('/api/attendance')
def attendance(request:Request, start:datetime|None=None, end:datetime|None=None, mode:str|None=None, db:Session=Depends(get_db)):
    u=require_user(request,db); stmt=scope_attendance(select(AttendanceEvent).order_by(desc(AttendanceEvent.occurred_at)),u)
    if start: stmt=stmt.where(AttendanceEvent.occurred_at>=as_utc(start))
    if end: stmt=stmt.where(AttendanceEvent.occurred_at<as_utc(end))
    if mode is not None and mode not in ('education','workplace'): raise HTTPException(422,'Invalid attendance mode')
    if mode in ('education','workplace'): stmt=stmt.where(AttendanceEvent.mode==mode)
    rows=db.scalars(stmt.limit(1000)).all(); overrides=correction_map(db, rows)
    return [event_dict(e, overrides.get(e.id)) for e in rows]

@app.get('/api/audit')
def audit_logs(request:Request,db:Session=Depends(get_db)):
    u=require_user(request,db); require_roles(u,'super_admin','org_admin','academic_admin','auditor')
    stmt=select(AuditLog).order_by(desc(AuditLog.created_at))
    if u.role=='academic_admin' and u.department:
        actors=select(User.username).where(User.department==u.department,User.role=='lecturer')
        stmt=stmt.where(AuditLog.actor.in_(actors)| (AuditLog.actor==u.username))
    return [{'id':a.id,'actor':a.actor,'action':a.action,'target':a.target,'detail':a.detail,'created_at':a.created_at.isoformat()} for a in db.scalars(stmt.limit(500)).all()]

@app.post('/api/attendance/{event_id}/correction')
def correct_event(event_id:int, request:Request, reason:str=Form(...), new_type:str=Form(...), db:Session=Depends(get_db)):
    u=require_user(request,db); require_roles(u,'super_admin','org_admin','hr_manager','academic_admin','lecturer')
    if len(reason.strip())<5: raise HTTPException(400,'A meaningful correction reason is required')
    e=db.get(AttendanceEvent,event_id)
    if not e: raise HTTPException(404,'Event not found')
    if u.role=='lecturer' and e.session_id not in db.scalars(assigned_session_ids(u)).all(): raise HTTPException(403,'You can only correct attendance for your assigned classes')
    if u.role=='academic_admin' and u.department and e.session_id not in db.scalars(assigned_session_ids(u)).all(): raise HTTPException(403,'This class is outside your department')
    allowed={'education':{'present','late','excused','absent'},'workplace':{'check_in','late','check_out','excused','absent'}}
    if new_type not in allowed[e.mode]: raise HTTPException(400,'Correction outcome does not match attendance mode')
    before=correction_map(db,[e]).get(e.id,e.event_type); db.add(Correction(event_id=e.id,before_value=before,after_value=new_type,reason=reason.strip(),actor=u.username)); db.commit(); audit(db,u.username,'attendance_correction_added',f'event:{e.id}',f'{before} -> {new_type}; {reason.strip()}'); return {'ok':True,'source_event_unchanged':True,'effective_type':new_type}

@app.get('/api/attendance/{event_id}/corrections')
def event_corrections(event_id:int, request:Request, db:Session=Depends(get_db)):
    u=require_user(request,db)
    event=db.get(AttendanceEvent,event_id)
    if not event: raise HTTPException(404,'Event not found')
    if u.role=='lecturer' and event.session_id not in db.scalars(assigned_session_ids(u)).all(): raise HTTPException(403,'You can only review your assigned classes')
    if u.role=='academic_admin' and u.department and event.session_id not in db.scalars(assigned_session_ids(u)).all(): raise HTTPException(403,'This class is outside your department')
    return [{'before':c.before_value,'after':c.after_value,'reason':c.reason,'actor':c.actor,'created_at':c.created_at.isoformat()} for c in db.scalars(select(Correction).where(Correction.event_id==event_id).order_by(Correction.created_at,Correction.id)).all()]

@app.get('/api/reports/attendance.csv')
def report_csv(request:Request,db:Session=Depends(get_db)):
    u=require_user(request,db); require_roles(u,'super_admin','org_admin','hr_manager','academic_admin','auditor')
    rows=db.scalars(scope_attendance(select(AttendanceEvent).order_by(AttendanceEvent.occurred_at),u)).all()
    buf=io.StringIO(); w=csv.writer(buf); w.writerow(['Event ID','Person Code','Person','Mode','Outcome','Timestamp','Source','Sync status','Note'])
    overrides=correction_map(db,rows)
    for e in rows: w.writerow([e.event_uid,e.person.person_code,e.person.full_name,e.mode,overrides.get(e.id,e.event_type),e.occurred_at.isoformat(),e.source,e.sync_status,e.note or ''])
    audit(db,u.username,'report_exported','attendance.csv',f'{len(rows)} rows')
    return StreamingResponse(iter([buf.getvalue()]),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="facetrack-attendance.csv"'})

@app.post('/api/sync')
def sync_events(request:Request, payload:list[dict], db:Session=Depends(get_db)):
    """Idempotent receipt endpoint for a future central server. This local build records receipt only."""
    u=require_user(request,db); accepted=0; duplicates=0
    for item in payload:
        uid=item.get('event_uid')
        if not uid: continue
        if db.scalar(select(SyncReceipt).where(SyncReceipt.event_uid==uid)): duplicates+=1; continue
        db.add(SyncReceipt(event_uid=uid)); accepted+=1
    db.commit(); audit(db,u.username,'sync_batch_received','sync',f'accepted={accepted}, duplicates={duplicates}')
    return {'accepted':accepted,'duplicates':duplicates,'note':'Receipt ledger is local in this build; configure a central deployment before production synchronization.'}


class UserIn(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=12, max_length=128)
    role: str
    department: str | None = Field(default=None, max_length=120)

@app.get('/api/users')
def list_users(request:Request, db:Session=Depends(get_db)):
    u=require_user(request,db); require_roles(u,'super_admin','org_admin','academic_admin')
    stmt=select(User).order_by(User.username)
    if u.role=='academic_admin':
        stmt=stmt.where(User.role=='lecturer')
        if u.department: stmt=stmt.where(User.department==u.department)
    role=request.query_params.get('role')
    if role: stmt=stmt.where(User.role==role)
    return [{'id':x.id,'username':x.username,'role':x.role,'department':x.department,'active':x.active} for x in db.scalars(stmt).all()]

@app.post('/api/users')
def create_user(data:UserIn, request:Request, db:Session=Depends(get_db)):
    u=require_user(request,db); require_roles(u,'super_admin','org_admin','academic_admin')
    allowed={'org_admin','hr_manager','academic_admin','lecturer','attendance_operator','auditor'}
    if data.role not in allowed: raise HTTPException(400,'Unsupported role')
    if u.role=='academic_admin' and data.role!='lecturer': raise HTTPException(403,'A department head can create teacher accounts only')
    if data.role=='academic_admin' and not data.department: raise HTTPException(400,'Set the department for this HOD account')
    if data.role=='lecturer' and not data.department: raise HTTPException(400,'Set the teacher department')
    if u.role=='academic_admin' and u.department and data.department!=u.department: raise HTTPException(403,'Teachers must belong to your department')
    if db.scalar(select(User).where(User.username==data.username)): raise HTTPException(409,'Username already exists')
    user=User(username=data.username,password_hash=hash_password(data.password),role=data.role,department=data.department)
    db.add(user); db.commit(); db.refresh(user); audit(db,u.username,'user_created',f'user:{user.id}',data.role)
    return {'id':user.id,'username':user.username,'role':user.role}

class UserUpdate(BaseModel):
    role: str | None = None
    active: bool | None = None

@app.patch('/api/users/{user_id}')
def update_user(user_id:int, data:UserUpdate, request:Request, db:Session=Depends(get_db)):
    actor=require_user(request,db); require_roles(actor,'super_admin','org_admin','academic_admin')
    target=db.get(User,user_id)
    if not target: raise HTTPException(404,'User not found')
    if actor.role=='academic_admin' and target.role!='lecturer': raise HTTPException(403,'Department heads can manage teacher accounts only')
    if actor.role=='academic_admin' and actor.department and target.department!=actor.department: raise HTTPException(403,'This teacher belongs to another department')
    if actor.role=='academic_admin' and data.role is not None and data.role!='lecturer': raise HTTPException(403,'Department heads cannot change teacher roles')
    allowed={'org_admin','hr_manager','academic_admin','lecturer','attendance_operator','auditor'}
    if data.role is not None:
        if data.role not in allowed: raise HTTPException(400,'Unsupported role')
        if actor.role!='super_admin' and data.role=='org_admin' and target.role!='org_admin':
            raise HTTPException(403,'Only a super administrator can grant organization admin')
        target.role=data.role
    if data.active is not None:
        if target.id==actor.id and not data.active: raise HTTPException(400,'You cannot deactivate your own account')
        if target.role=='super_admin' and not data.active and db.scalar(select(func.count(User.id)).where(User.role=='super_admin',User.active==True))<=1:
            raise HTTPException(400,'At least one active super administrator is required')
        target.active=data.active
        if not data.active:
            for session in db.scalars(select(UserSession).where(UserSession.user_id==target.id,UserSession.revoked_at.is_(None))).all():
                session.revoked_at=datetime.now(timezone.utc)
    db.commit(); audit(db,actor.username,'user_updated',f'user:{target.id}',f'role={target.role}; active={target.active}')
    return {'id':target.id,'username':target.username,'role':target.role,'active':target.active}

@app.post('/api/account/change-password')
def change_password(request:Request, current_password:str=Form(...), new_password:str=Form(...), db:Session=Depends(get_db)):
    u=require_user(request,db)
    if len(new_password)<12: raise HTTPException(400,'New password must be at least 12 characters')
    if not verify_password(current_password,u.password_hash): raise HTTPException(400,'Current password is incorrect')
    u.password_hash=hash_password(new_password)
    now=datetime.now(timezone.utc)
    for session in db.scalars(select(UserSession).where(UserSession.user_id==u.id,UserSession.revoked_at.is_(None))).all(): session.revoked_at=now
    db.commit(); audit(db,u.username,'password_changed',f'user:{u.id}')
    return {'ok':True}

@app.get('/api/recognition/status')
def recognition_status(request:Request,db:Session=Depends(get_db)):
    require_user(request,db)
    return model_status()

@app.post('/api/people/{person_id}/biometrics')
async def enroll_biometrics(person_id:int, request:Request, consent_confirmed:bool=Form(False),
                            replace_existing:bool=Form(False), images:list[UploadFile]=File(...),
                            db:Session=Depends(get_db)):
    operator=require_user(request,db); require_roles(operator,'super_admin','org_admin','hr_manager','academic_admin')
    person=db.get(Person,person_id)
    if not person or person.status!='active': raise HTTPException(404,'Active person not found')
    if operator.role=='academic_admin' and operator.department and (person.group_name or '').strip().casefold() not in db.scalars(assigned_group_names(operator)).all(): raise HTTPException(403,'This student is outside your department')
    if not consent_confirmed: raise HTTPException(400,'Record the person\'s informed consent before enrollment')
    if len(images)<3 or len(images)>5: raise HTTPException(400,'Capture three to five clear face samples')
    existing=db.scalar(select(BiometricTemplate).where(BiometricTemplate.person_id==person.id))
    if existing and not replace_existing: raise HTTPException(409,'A face template already exists; confirm replacement explicitly')
    import numpy as np
    embeddings=[]
    for image in images:
        raw=await image.read(5*1024*1024+1)
        if len(raw)>5*1024*1024: raise HTTPException(413,'Each image must be 5 MB or smaller')
        try: embeddings.append(extract_embedding(raw))
        except RuntimeError as error: raise HTTPException(503,str(error))
        except ValueError as error: raise HTTPException(422,str(error))
    template=np.mean(np.stack(embeddings),axis=0); template=template/np.linalg.norm(template)
    try: protected=encrypt_embedding(template)
    except RuntimeError as error: raise HTTPException(503,str(error))
    if existing:
        existing.encrypted_embedding=protected; existing.consent_confirmed=True
        existing.consent_at=datetime.now(timezone.utc); existing.enrolled_by=operator.id
    else:
        db.add(BiometricTemplate(person_id=person.id,encrypted_embedding=protected,
                                 consent_confirmed=True,enrolled_by=operator.id))
    db.commit(); audit(db,operator.username,'biometric_enrolled',f'person:{person.id}','Encrypted feature vector only; source images discarded')
    return {'ok':True,'person_id':person.id,'samples':len(embeddings),'images_retained':False}

@app.delete('/api/people/{person_id}/biometrics')
def delete_biometrics(person_id:int, request:Request, db:Session=Depends(get_db)):
    operator=require_user(request,db); require_roles(operator,'super_admin','org_admin','hr_manager','academic_admin')
    template=db.scalar(select(BiometricTemplate).where(BiometricTemplate.person_id==person_id))
    if not template: raise HTTPException(404,'No biometric template found')
    if operator.role=='academic_admin' and operator.department and (template.person.group_name or '').strip().casefold() not in db.scalars(assigned_group_names(operator)).all(): raise HTTPException(403,'This student is outside your department')
    db.delete(template); db.commit(); audit(db,operator.username,'biometric_deleted',f'person:{person_id}')
    return {'ok':True,'template_deleted':True,'recognition_logs_retained':True}

@app.post('/api/recognition/match')
async def recognize_face(request:Request, image:UploadFile=File(...), db:Session=Depends(get_db)):
    operator=require_user(request,db)
    raw=await image.read(5*1024*1024+1)
    if len(raw)>5*1024*1024: raise HTTPException(413,'Image must be 5 MB or smaller')
    try: probe=extract_embedding(raw)
    except RuntimeError as error: raise HTTPException(503,str(error))
    except ValueError as error: raise HTTPException(422,str(error))
    template_query=select(BiometricTemplate).join(Person).where(Person.status=='active',BiometricTemplate.consent_confirmed==True)
    if operator.role=='lecturer' or (operator.role=='academic_admin' and operator.department):
        template_query=template_query.where(func.lower(func.trim(Person.group_name)).in_(assigned_group_names(operator)))
    templates=db.scalars(template_query).all()
    scored=[]
    for template in templates:
        try: enrolled=decrypt_embedding(template.encrypted_embedding)
        except RuntimeError as error: raise HTTPException(503,str(error))
        scored.append((float(np.dot(probe,enrolled)),template.person))
    scored.sort(key=lambda pair:pair[0],reverse=True)
    best_score,best_person=scored[0] if scored else (None,None)
    runner_up=scored[1][0] if len(scored)>1 else -1.0
    threshold=match_threshold()
    recognized=best_person is not None and best_score>=threshold and best_score-runner_up>=0.025
    db.add(RecognitionLog(person_id=best_person.id if recognized else None,confidence_score=best_score,
                          recognition_status='recognized' if recognized else ('ambiguous' if best_person and best_score>=threshold else 'unknown'),
                          operator_id=operator.id))
    db.commit()
    if not recognized:
        return {'status':'ambiguous' if best_person and best_score>=threshold else 'unknown','confidence':round(best_score,4) if best_score is not None else None,'person':None,'attendance_written':False,'operator_confirmation_required':True}
    return {'status':'recognized','confidence':round(best_score,4),'person':person_dict(best_person,True),
            'attendance_written':False,'operator_confirmation_required':True,
            'message':'Verify the person in front of you, then confirm attendance. This build has no presentation-attack detector.'}

@app.get('/api/recognition/logs')
def recognition_logs(request:Request, db:Session=Depends(get_db)):
    u=require_user(request,db); require_roles(u,'super_admin','org_admin','academic_admin','auditor')
    stmt=select(RecognitionLog).order_by(desc(RecognitionLog.recognized_at))
    if u.role=='academic_admin' and u.department:
        operators=select(User.id).where(User.department==u.department,User.role=='lecturer')
        stmt=stmt.where(RecognitionLog.operator_id.in_(operators)| (RecognitionLog.operator_id==u.id))
    rows=db.scalars(stmt.limit(500)).all()
    return [{'recognized_at':r.recognized_at.isoformat(),'person':r.person.full_name if r.person else None,
             'status':r.recognition_status,'confidence':r.confidence_score,
             'operator':r.operator.username if r.operator else 'unknown'} for r in rows]

@app.get('/api/health')
def health():
    recognition = model_status()
    return {'status':'ok','database':'local','recognition':'ready' if recognition['configured'] else 'not_configured','version':app.version}
