from flask import (Flask,render_template,request,redirect,
                   url_for,flash,session,Response,jsonify)
from werkzeug.security import check_password_hash
from functools import wraps
import re,csv,os,json,time,threading,secrets,string
import requests as req
from config import (SECRET_KEY,SUPABASE_URL,SUPABASE_ANON_KEY,SUPABASE_SERVICE_KEY)

app=Flask(__name__)
app.secret_key=SECRET_KEY
BLOOD_GROUPS=['A+','A-','B+','B-','AB+','AB-','O+','O-']
CSV_DIR=os.path.join(os.path.dirname(os.path.abspath(__file__)),'csv')
_change_event=threading.Event()
def _notify(): _change_event.set()

_ANON={'apikey':SUPABASE_ANON_KEY,'Content-Type':'application/json'}
_SVC={'apikey':SUPABASE_SERVICE_KEY,'Authorization':f'Bearer {SUPABASE_SERVICE_KEY}',
      'Content-Type':'application/json','Prefer':'return=representation'}

def db_get(table,filters=None,select='*',order=None,limit=None):
    p=f'select={select}'
    if filters:
        for k,v in filters.items(): p+=f'&{k}={v}'
    if order: p+=f'&order={order}'
    if limit: p+=f'&limit={limit}'
    try: return req.get(f'{SUPABASE_URL}/rest/v1/{table}?{p}',headers=_SVC).json()
    except: return []

def db_insert(table,data):
    try:
        r=req.post(f'{SUPABASE_URL}/rest/v1/{table}',headers=_SVC,json=data).json()
        return r[0] if isinstance(r,list) and r else r
    except: return None

def db_update(table,filters,data):
    p='&'.join(f'{k}={v}' for k,v in filters.items())
    try: return req.patch(f'{SUPABASE_URL}/rest/v1/{table}?{p}',headers=_SVC,json=data).json()
    except: return None

def db_delete(table,filters):
    p='&'.join(f'{k}={v}' for k,v in filters.items())
    return req.delete(f'{SUPABASE_URL}/rest/v1/{table}?{p}',headers=_SVC).status_code

def sb_signup(email,pw,meta):
    return req.post(f'{SUPABASE_URL}/auth/v1/signup',headers=_ANON,
        json={'email':email,'password':pw,'data':meta}).json()

def sb_signin(email,pw):
    return req.post(f'{SUPABASE_URL}/auth/v1/token?grant_type=password',
        headers=_ANON,json={'email':email,'password':pw}).json()

def sb_get_user(jwt):
    return req.get(f'{SUPABASE_URL}/auth/v1/user',
        headers={**_ANON,'Authorization':f'Bearer {jwt}'}).json()

def sb_update_user(jwt,data):
    return req.put(f'{SUPABASE_URL}/auth/v1/user',
        headers={**_ANON,'Authorization':f'Bearer {jwt}'},json=data).json()

def sb_recover(email):
    redir=request.host_url.rstrip('/')+url_for('reset_password')
    return req.post(f'{SUPABASE_URL}/auth/v1/recover',headers=_ANON,
        json={'email':email,'redirect_to':redir}).json()

def sb_admin_get_user(uid):
    h={'apikey':SUPABASE_SERVICE_KEY,'Authorization':f'Bearer {SUPABASE_SERVICE_KEY}'}
    try: return req.get(f'{SUPABASE_URL}/auth/v1/admin/users/{uid}',headers=h).json()
    except: return {}

def sb_google_url():
    redir=request.host_url.rstrip('/')+'/auth/callback'
    return f'{SUPABASE_URL}/auth/v1/authorize?provider=google&redirect_to={redir}'

def get_profile(uid):
    rows=db_get('user_profiles',{'id':f'eq.{uid}'})
    return rows[0] if isinstance(rows,list) and rows else None

def create_profile(uid,username,full_name,phone=None):
    return db_insert('user_profiles',
        {'id':uid,'username':username,'full_name':full_name,'phone':phone})

def update_profile(uid,data):
    return db_update('user_profiles',{'id':f'eq.{uid}'},data)

def username_exists(u,exclude_uid=None):
    rows=db_get('user_profiles',{'username':f'eq.{u}'},'id')
    if not isinstance(rows,list) or not rows: return False
    if exclude_uid: return any(r['id']!=exclude_uid for r in rows)
    return True

def generate_username(full_name):
    base=re.sub(r'[^a-z]','',full_name.lower().replace(' ',''))[:20] or 'user'
    u=base; n=1
    while username_exists(u): u=base+str(n); n+=1
    return u

def gen_password():
    chars=string.ascii_letters+string.digits+'!@#$%^&*()'
    while True:
        p=''.join(secrets.choice(chars) for _ in range(14))
        if(any(c.isupper() for c in p) and any(c.islower() for c in p)
           and any(c.isdigit() for c in p) and any(c in '!@#$%^&*()' for c in p)):
            return p

def valid_name(n): return bool(re.match(r'^[A-Za-z.\s]+$',n.strip())) and len(n.strip())>=2
def valid_username(u): return bool(re.match(r'^[a-z][a-z_]*[0-9]*$',u)) and len(u)>=3
def valid_phone(p): return bool(re.match(r'^03\d{2}-\d{7}$',p.strip()))
def valid_email(e): return bool(re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$',e.strip()))
def valid_password(p):
    return(len(p)>=8 and re.search(r'[A-Z]',p) and re.search(r'[a-z]',p)
           and re.search(r'\d',p) and re.search(r'[!@#$%^&*(),.?:{}|<>]',p))

def login_required(f):
    @wraps(f)
    def dec(*a,**kw):
        if 'user_type' not in session:
            flash('Please log in to continue.','warning')
            return redirect(url_for('landing'))
        return f(*a,**kw)
    return dec

def admin_required(f):
    @wraps(f)
    def dec(*a,**kw):
        if session.get('user_type')!='admin':
            flash('Admin access required.','danger')
            return redirect(url_for('admin_login'))
        return f(*a,**kw)
    return dec

def user_required(f):
    @wraps(f)
    def dec(*a,**kw):
        if session.get('user_type')!='user':
            flash('Please log in first.','warning')
            return redirect(url_for('login'))
        return f(*a,**kw)
    return dec

_csv_written={}

def sync_csv(table):
    try:
        os.makedirs(CSV_DIR,exist_ok=True)
        rows=db_get(table)
        if not isinstance(rows,list) or not rows: return
        fp=os.path.join(CSV_DIR,f'{table.lower()}.csv')
        with open(fp,'w',newline='',encoding='utf-8') as f:
            w=csv.DictWriter(f,fieldnames=rows[0].keys())
            w.writeheader(); w.writerows(rows)
        _csv_written[fp]=time.time(); _notify()
    except Exception as e: print(f'[CSV] {table}: {e}')

def _start_watchdog():
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
        _C={'donor':{'donor_id':int,'area_id':int,'age':int,
                     'is_available':lambda x:x in('1','True','true',True)},
            'donation_record':{'donation_id':int,'donor_id':int,'units_donated':int},
            'blood_request':{'request_id':int,
                             'admin_id':lambda x:int(x) if x and str(x).strip() else None},
            'area':{'area_id':int},'admin_user':{'admin_id':int}}
        class H(FileSystemEventHandler):
            _db={}
            def on_modified(self,ev):
                if ev.is_directory or not ev.src_path.endswith('.csv'): return
                fp=ev.src_path; now=time.time()
                if now-self._db.get(fp,0)<2: return
                self._db[fp]=now
                if now-_csv_written.get(fp,0)<3: return
                tbl=os.path.splitext(os.path.basename(fp))[0]
                try:
                    with open(fp,'r',encoding='utf-8') as f:
                        rows=list(csv.DictReader(f))
                    casts=_C.get(tbl,{})
                    for row in rows:
                        out={k:(casts[k](v) if k in casts and v else(None if v=='' else v))
                             for k,v in row.items()}
                        db_insert(tbl,out)
                    _notify()
                except Exception as e: print(f'[WATCHDOG] {tbl}: {e}')
        os.makedirs(CSV_DIR,exist_ok=True)
        obs=Observer(); obs.schedule(H(),CSV_DIR,recursive=False); obs.start()
        print(f'[WATCHDOG] Watching {CSV_DIR}')
    except Exception as e: print(f'[WATCHDOG] {e}')

threading.Thread(target=_start_watchdog,daemon=True).start()

def _stats():
    try:
        td=db_get('donor',select='donor_id')
        av=db_get('donor',{'is_available':'eq.true'},'donor_id')
        pr=db_get('blood_request',{'status':'eq.Pending'},'request_id')
        dn=db_get('donation_record',select='donation_id')
        return{'total_donors':len(td) if isinstance(td,list) else 0,
               'available_donors':len(av) if isinstance(av,list) else 0,
               'pending_requests':len(pr) if isinstance(pr,list) else 0,
               'total_donations':len(dn) if isinstance(dn,list) else 0}
    except: return{'total_donors':0,'available_donors':0,'pending_requests':0,'total_donations':0}

@app.route('/stream')
def stream():
    def gen():
        yield f"data:{json.dumps(_stats())}\n\n"
        while True:
            _change_event.wait(timeout=30); _change_event.clear()
            yield f"data:{json.dumps(_stats())}\n\n"
    return Response(gen(),mimetype='text/event-stream',
                    headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})

@app.route('/api/stats')
def api_stats(): return jsonify(_stats())

@app.route('/api/check-username')
def check_username():
    u=request.args.get('username','').strip().lower()
    if not u or not valid_username(u): return jsonify({'available':False})
    return jsonify({'available':not username_exists(u,exclude_uid=session.get('user_id'))})

@app.route('/api/set-phone',methods=['POST'])
@user_required
def api_set_phone():
    phone=(request.get_json() or {}).get('phone','').strip()
    if not valid_phone(phone): return jsonify({'ok':False})
    update_profile(session['user_id'],{'phone':phone})
    return jsonify({'ok':True})

@app.route('/')
def landing():
    if session.get('user_type')=='admin': return redirect(url_for('admin'))
    if session.get('user_type')=='user':  return redirect(url_for('home'))
    return render_template('landing.html')

@app.route('/home')
@login_required
def home():
    s=_stats()
    raw=db_get('donor',{'is_available':'eq.true'},'blood_group')
    amap={}
    if isinstance(raw,list):
        for r in raw: amap[r['blood_group']]=amap.get(r['blood_group'],0)+1
    recent=db_get('blood_request',{'status':'eq.Pending'},
        'requester_name,blood_group_needed,urgency,contact_number,request_date',
        order='request_date.asc',limit=5)
    return render_template('index.html',
        total_donors=s['total_donors'],available_donors=s['available_donors'],
        pending_requests=s['pending_requests'],total_donations=s['total_donations'],
        blood_group_stats=[{'group':g,'count':amap.get(g,0)} for g in BLOOD_GROUPS],
        recent_requests=recent if isinstance(recent,list) else [])

@app.route('/signup',methods=['GET','POST'])
def signup():
    if session.get('user_type')=='user': return redirect(url_for('home'))
    if request.method=='POST':
        email=request.form.get('email','').strip().lower()
        uname=request.form.get('username','').strip().lower()
        fname=request.form.get('full_name','').strip()
        phone=request.form.get('phone','').strip()
        pw=request.form.get('password','')
        pw2=request.form.get('confirm_password','')
        errors=[]
        if not valid_name(fname):     errors.append('Name: letters, dots and spaces only.')
        if not valid_username(uname): errors.append('Username: lowercase, digits at end only.')
        if username_exists(uname):    errors.append('Username already taken.')
        if not valid_email(email):    errors.append('Enter a valid email address.')
        if not valid_phone(phone):    errors.append('Phone must be 03XX-XXXXXXX format.')
        if not valid_password(pw):    errors.append('Password: 8+ chars, upper, lower, digit, symbol.')
        if pw!=pw2:                   errors.append('Passwords do not match.')
        if errors:
            for e in errors: flash(e,'danger')
        else:
            r=sb_signup(email,pw,{'username':uname,'full_name':fname,'phone':phone})
            if r.get('error'):
                flash(f"Signup failed: {r['error'].get('message','')}",'danger')
            else:
                uid=(r.get('user') or {}).get('id') or r.get('id')
                if uid: create_profile(uid,uname,fname,phone)
                flash('✅ Account created! Check your email for the confirmation link.','success')
                return redirect(url_for('login'))
    return render_template('signup.html',google_url=sb_google_url())

@app.route('/login',methods=['GET','POST'])
def login():
    if session.get('user_type')=='user': return redirect(url_for('home'))
    if request.method=='POST':
        identifier=request.form.get('identifier','').strip().lower()
        pw=request.form.get('password','')
        email=identifier
        if '@' not in identifier:
            rows=db_get('user_profiles',{'username':f'eq.{identifier}'},'id')
            if isinstance(rows,list) and rows:
                u=sb_admin_get_user(rows[0]['id'])
                email=u.get('email',identifier)
            else:
                flash('No account found with that username.','danger')
                return render_template('login.html',google_url=sb_google_url())
        r=sb_signin(email,pw)
        if r.get('error'):
            msg=r['error'].get('message','Login failed.')
            flash('Please confirm your email first.' if 'not confirmed' in msg
                  else f'Login failed: {msg}',
                  'warning' if 'not confirmed' in msg else 'danger')
        elif r.get('access_token'):
            jwt=r['access_token']; uid=r['user']['id']
            prof=get_profile(uid)
            if not prof:
                meta=r['user'].get('user_metadata',{})
                create_profile(uid,meta.get('username',email.split('@')[0]),
                               meta.get('full_name',''),meta.get('phone'))
                prof=get_profile(uid)
            session.update({'user_type':'user','user_id':uid,'jwt':jwt,
                'username':prof.get('username','') if prof else '',
                'full_name':prof.get('full_name','') if prof else ''})
            flash(f"Welcome back, {session['full_name'] or session['username']}!",'success')
            return redirect(url_for('home'))
        else: flash('Login failed.','danger')
    return render_template('login.html',google_url=sb_google_url())

@app.route('/auth/callback')
def auth_callback(): return render_template('auth_callback.html')

@app.route('/auth/session',methods=['POST'])
def auth_session():
    jwt=(request.get_json() or {}).get('access_token')
    if not jwt: return jsonify({'ok':False})
    user=sb_get_user(jwt)
    if user.get('error'): return jsonify({'ok':False})
    uid=user['id']; prof=get_profile(uid)
    if not prof:
        meta=user.get('user_metadata',{})
        fname=meta.get('full_name','') or meta.get('name','') or user.get('email','').split('@')[0]
        uname=generate_username(fname)
        sb_update_user(jwt,{'password':gen_password()})
        create_profile(uid,uname,fname,None)
        prof=get_profile(uid)
    session.update({'user_type':'user','user_id':uid,'jwt':jwt,
        'username':prof.get('username','') if prof else '',
        'full_name':prof.get('full_name','') if prof else ''})
    return jsonify({'ok':True,'needs_phone':not(prof.get('phone') if prof else None)})

@app.route('/logout')
def logout():
    name=session.get('full_name',''); session.clear()
    flash(f'Goodbye{", "+name if name else ""}!','info')
    return redirect(url_for('landing'))

@app.route('/forgot-password',methods=['GET','POST'])
def forgot_password():
    if request.method=='POST':
        email=request.form.get('email','').strip().lower()
        if valid_email(email): sb_recover(email)
        flash('If that email is registered, a reset link has been sent.','info')
        return redirect(url_for('login'))
    return render_template('forgot_password.html')

@app.route('/reset-password')
def reset_password(): return render_template('reset_password.html')

@app.route('/reset-password/submit',methods=['POST'])
def reset_password_submit():
    jwt=request.form.get('access_token','')
    pw=request.form.get('password','')
    pw2=request.form.get('confirm_password','')
    if not jwt: flash('Invalid or expired reset link.','danger'); return redirect(url_for('login'))
    if not valid_password(pw): flash('Password: 8+ chars, upper, lower, digit, symbol.','danger'); return redirect(url_for('reset_password'))
    if pw!=pw2: flash('Passwords do not match.','danger'); return redirect(url_for('reset_password'))
    r=sb_update_user(jwt,{'password':pw})
    flash('✅ Password updated! Please log in.' if not r.get('error') else 'Reset failed.',
          'success' if not r.get('error') else 'danger')
    return redirect(url_for('login'))

@app.route('/admin/login',methods=['GET','POST'])
def admin_login():
    if session.get('user_type')=='admin': return redirect(url_for('admin'))
    if request.method=='POST':
        uname=request.form.get('username','').strip()
        pw=request.form.get('password','')
        rows=db_get('admin_user',{'username':f'eq.{uname}'})
        if isinstance(rows,list) and rows:
            adm=rows[0]
            if adm.get('password_hash') and check_password_hash(adm['password_hash'],pw):
                session.update({'user_type':'admin','admin_id':adm['admin_id'],
                    'username':adm['username'],'full_name':adm['full_name']})
                flash(f"Welcome, {adm['full_name']}!",'success')
                return redirect(url_for('admin'))
        flash('Invalid username or password.','danger')
    return render_template('admin_login.html')

@app.route('/admin/logout')
def admin_logout():
    session.clear(); flash('Admin session ended.','info')
    return redirect(url_for('admin_login'))

@app.route('/settings')
@user_required
def settings():
    uid=session['user_id']; prof=get_profile(uid) or {}
    donor_row=None
    if prof.get('donor_id'):
        rows=db_get('donor',{'donor_id':f'eq.{prof["donor_id"]}'},'*',limit=1)
        if isinstance(rows,list) and rows:
            donor_row=rows[0]
            a=db_get('area',{'area_id':f'eq.{donor_row["area_id"]}'},'area_name',limit=1)
            donor_row['area_name']=a[0]['area_name'] if isinstance(a,list) and a else ''
    return render_template('settings.html',profile=prof,donor_row=donor_row)

@app.route('/settings/name',methods=['GET','POST'])
@user_required
def settings_name():
    uid=session['user_id']
    if request.method=='POST':
        fname=request.form.get('full_name','').strip()
        if not valid_name(fname): flash('Letters, dots and spaces only.','danger')
        else:
            update_profile(uid,{'full_name':fname}); session['full_name']=fname
            flash('✅ Name updated.','success'); return redirect(url_for('settings'))
    return render_template('settings_name.html',profile=get_profile(uid) or {})

@app.route('/settings/username',methods=['GET','POST'])
@user_required
def settings_username():
    uid=session['user_id']
    if request.method=='POST':
        uname=request.form.get('username','').strip().lower()
        if not valid_username(uname): flash('Invalid username format.','danger')
        elif username_exists(uname,exclude_uid=uid): flash('Username already taken.','danger')
        else:
            update_profile(uid,{'username':uname}); session['username']=uname
            flash('✅ Username updated.','success'); return redirect(url_for('settings'))
    return render_template('settings_username.html',profile=get_profile(uid) or {})

@app.route('/settings/email',methods=['GET','POST'])
@user_required
def settings_email():
    jwt=session['jwt']
    if request.method=='POST':
        new_email=request.form.get('new_email','').strip().lower()
        if not valid_email(new_email): flash('Enter a valid email address.','danger')
        else:
            r=sb_update_user(jwt,{'email':new_email})
            if r.get('error'): flash(f"Update failed: {r['error'].get('message','')}","danger")
            else:
                flash('✅ Confirmation sent to both emails. Click the link in your NEW email to complete the change.','success')
                return redirect(url_for('settings'))
    return render_template('settings_email.html')

@app.route('/settings/phone',methods=['GET','POST'])
@user_required
def settings_phone():
    uid=session['user_id']; prof=get_profile(uid) or {}
    if request.method=='POST':
        new_phone=request.form.get('phone','').strip()
        if not valid_phone(new_phone):
            flash('Phone must be in 03XX-XXXXXXX format.','danger')
        else:
            update_profile(uid,{'phone':new_phone})
            flash('✅ Phone number updated successfully.','success')
            return redirect(url_for('settings'))
    return render_template('settings_phone.html',profile=prof)

@app.route('/settings/password',methods=['GET','POST'])
@user_required
def settings_password():
    jwt=session['jwt']
    if request.method=='POST':
        current=request.form.get('current_password','')
        new_pw=request.form.get('new_password','')
        confirm=request.form.get('confirm_password','')
        if not valid_password(new_pw): flash('Password: 8+ chars, upper, lower, digit, symbol.','danger')
        elif new_pw!=confirm: flash('Passwords do not match.','danger')
        else:
            user=sb_get_user(jwt); email=user.get('email','')
            check=sb_signin(email,current)
            if check.get('error'): flash('Current password is incorrect.','danger')
            else:
                r=sb_update_user(jwt,{'password':new_pw})
                if r.get('error'): flash('Password change failed.','danger')
                else:
                    flash('✅ Password changed successfully.','success')
                    return redirect(url_for('settings'))
    return render_template('settings_password.html')

@app.route('/settings/availability',methods=['POST'])
@user_required
def settings_availability():
    uid=session['user_id']; prof=get_profile(uid) or {}
    if prof.get('donor_id'):
        rows=db_get('donor',{'donor_id':f'eq.{prof["donor_id"]}'},'is_available',limit=1)
        if isinstance(rows,list) and rows:
            new=not rows[0].get('is_available',True)
            db_update('donor',{'donor_id':f'eq.{prof["donor_id"]}'},{'is_available':new})
            sync_csv('donor')
            flash(f"✅ Marked as {'Available' if new else 'Unavailable'}.",'success')
    return redirect(url_for('settings'))

@app.route('/search')
@login_required
def search():
    areas=db_get('area',select='area_id,area_name',order='area_name.asc')
    if not isinstance(areas,list): areas=[]
    bg=request.args.get('blood_group','').strip()
    aid=request.args.get('area_id','').strip()
    donors=[]; searched=bool(bg or aid)
    if searched:
        filt={'is_available':'eq.true'}
        if bg: filt['blood_group']=f'eq.{bg}'
        if aid: filt['area_id']=f'eq.{aid}'
        raw=db_get('donor',filt,'full_name,blood_group,phone,age,gender,area_id',order='full_name.asc')
        if isinstance(raw,list):
            for r in raw:
                a=db_get('area',{'area_id':f'eq.{r["area_id"]}'},'area_name',limit=1)
                r['area_name']=a[0]['area_name'] if isinstance(a,list) and a else ''
                donors.append(r)
    return render_template('search.html',areas=areas,donors=donors,
        searched=searched,selected_bg=bg,selected_area_id=aid,blood_groups=BLOOD_GROUPS)

@app.route('/register',methods=['GET','POST'])
@login_required
def register():
    areas=db_get('area',select='area_id,area_name',order='area_name.asc')
    if not isinstance(areas,list): areas=[]
    if request.method=='POST':
        fname=request.form.get('full_name','').strip()
        bg=request.form.get('blood_group','')
        age=request.form.get('age','')
        gender=request.form.get('gender','')
        phone=request.form.get('phone','').strip()
        area=request.form.get('area_id','')
        errors=[]
        if not valid_name(fname):                       errors.append('Name: letters, dots, spaces only.')
        if bg not in BLOOD_GROUPS:                      errors.append('Select a valid blood group.')
        if not age.isdigit() or not(18<=int(age)<=65): errors.append('Age must be 18-65.')
        if gender not in('Male','Female','Other'):      errors.append('Select a gender.')
        if not valid_phone(phone):                      errors.append('Phone: 03XX-XXXXXXX format.')
        if not area.isdigit():                          errors.append('Select your area.')
        if errors:
            for e in errors: flash(e,'danger')
        else:
            dup=db_get('donor',{'phone':f'eq.{phone}'},'donor_id')
            if isinstance(dup,list) and dup: flash('❌ Phone already registered.','danger')
            else:
                new=db_insert('donor',{'area_id':int(area),'full_name':fname,
                    'blood_group':bg,'age':int(age),'gender':gender,
                    'phone':phone,'is_available':True})
                if new and isinstance(new,dict) and new.get('donor_id'):
                    if session.get('user_type')=='user':
                        update_profile(session['user_id'],
                            {'donor_id':new['donor_id'],'phone':phone})
                    sync_csv('donor')
                    flash(f'✅ {fname} registered as a donor!','success')
                    return redirect(url_for('register'))
                else: flash('Registration failed. Try again.','danger')
    return render_template('register.html',areas=areas,blood_groups=BLOOD_GROUPS)

@app.route('/request',methods=['GET','POST'])
@login_required
def blood_request():
    if request.method=='POST':
        name=request.form.get('requester_name','').strip()
        bg=request.form.get('blood_group_needed','')
        urgency=request.form.get('urgency','Normal')
        contact=request.form.get('contact_number','').strip()
        errors=[]
        if not valid_name(name):                       errors.append('Name: letters, dots, spaces only.')
        if bg not in BLOOD_GROUPS:                     errors.append('Select a valid blood group.')
        if urgency not in('Critical','High','Normal'): errors.append('Select urgency level.')
        if not valid_phone(contact):                   errors.append('Contact: 03XX-XXXXXXX format.')
        if errors:
            for e in errors: flash(e,'danger')
        else:
            dup=db_get('blood_request',
                {'contact_number':f'eq.{contact}','blood_group_needed':f'eq.{bg}',
                 'urgency':f'eq.{urgency}','status':'eq.Pending'},'request_id')
            if isinstance(dup,list) and dup:
                flash('⚠️ You already have a pending request with these details.','warning')
                return redirect(url_for('blood_request'))
            new=db_insert('blood_request',{'admin_id':session.get('admin_id'),
                'requester_name':name,'blood_group_needed':bg,
                'urgency':urgency,'contact_number':contact})
            if new:
                sync_csv('blood_request')
                flash('✅ Request submitted!','success')
                return redirect(url_for('blood_request'))
            flash('Submission failed. Try again.','danger')
    return render_template('request.html',blood_groups=BLOOD_GROUPS)

@app.route('/admin')
@admin_required
def admin():
    s=_stats()
    raw=db_get('donor',select='blood_group,is_available')
    bg_map={}
    if isinstance(raw,list):
        for r in raw:
            g=r['blood_group']
            if g not in bg_map: bg_map[g]={'blood_group':g,'total':0,'available':0}
            bg_map[g]['total']+=1
            if r.get('is_available'): bg_map[g]['available']+=1
    pending=db_get('blood_request',{'status':'eq.Pending'},'*',order='request_date.asc')
    donations=db_get('donation_record',select='*',order='donation_date.desc',limit=10)
    if isinstance(donations,list):
        for d in donations:
            rows=db_get('donor',{'donor_id':f'eq.{d["donor_id"]}'},'full_name,blood_group',limit=1)
            if isinstance(rows,list) and rows:
                d['full_name']=rows[0].get('full_name','')
                d['blood_group']=rows[0].get('blood_group','')
    recent_donors=db_get('donor',
        select='donor_id,full_name,blood_group,phone,area_id,is_available,registered_on',
        order='registered_on.desc',limit=15)
    if isinstance(recent_donors,list):
        for d in recent_donors:
            a=db_get('area',{'area_id':f'eq.{d["area_id"]}'},'area_name',limit=1)
            d['area_name']=a[0]['area_name'] if isinstance(a,list) and a else ''
    return render_template('admin.html',
        total_donors=s['total_donors'],available_donors=s['available_donors'],
        pending_requests=s['pending_requests'],total_donations=s['total_donations'],
        blood_group_breakdown=list(bg_map.values()),
        pending_blood_requests=pending if isinstance(pending,list) else [],
        recent_donations=donations if isinstance(donations,list) else [],
        recent_donors=recent_donors if isinstance(recent_donors,list) else [])

@app.route('/admin/fulfill/<int:rid>')
@admin_required
def fulfill_request(rid):
    db_update('blood_request',{'request_id':f'eq.{rid}','status':'eq.Pending'},
              {'status':'Fulfilled','admin_id':session['admin_id']})
    sync_csv('blood_request'); flash(f'✅ Request #{rid} fulfilled.','success')
    return redirect(url_for('admin'))

@app.route('/admin/cancel/<int:rid>')
@admin_required
def cancel_request(rid):
    db_update('blood_request',{'request_id':f'eq.{rid}'},{'status':'Cancelled'})
    sync_csv('blood_request'); flash(f'Request #{rid} cancelled.','info')
    return redirect(url_for('admin'))

@app.route('/admin/toggle-donor/<int:did>')
@admin_required
def toggle_donor(did):
    rows=db_get('donor',{'donor_id':f'eq.{did}'},'is_available',limit=1)
    if isinstance(rows,list) and rows:
        db_update('donor',{'donor_id':f'eq.{did}'},
                  {'is_available':not rows[0].get('is_available',True)})
        sync_csv('donor')
    return redirect(url_for('admin'))

@app.route('/admin/export-all')
@admin_required
def export_all():
    for t in ['area','admin_user','donor','donation_record','blood_request']: sync_csv(t)
    flash('✅ All CSV files refreshed from Supabase.','success')
    return redirect(url_for('admin'))

if __name__=='__main__':
    port=int(os.environ.get('PORT',5000))
    print(f'BloodBank Peshawar | http://127.0.0.1:{port}')
    app.run(host='0.0.0.0',port=port,debug=True,use_reloader=False,threaded=True)
