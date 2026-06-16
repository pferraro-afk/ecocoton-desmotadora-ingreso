import os
import re
from datetime import date, datetime, timedelta
from functools import wraps

from flask import (Flask, flash, redirect, render_template,
                   request, session, url_for)
from werkzeug.utils import secure_filename

from database import get_db, init_db, migrate_db

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'desmotadora-clave-2024')

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'static', 'uploads')
ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg'}
PORTERO_PASSWORD = os.environ.get('PORTERO_PASSWORD', 'portero123')

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB

MESES = {
    1: 'Enero', 2: 'Febrero', 3: 'Marzo', 4: 'Abril',
    5: 'Mayo', 6: 'Junio', 7: 'Julio', 8: 'Agosto',
    9: 'Septiembre', 10: 'Octubre', 11: 'Noviembre', 12: 'Diciembre',
}

init_db()
migrate_db()


# ── helpers ──────────────────────────────────────────────────────────────────

def normalizar_dni(raw):
    return re.sub(r'\D', '', raw or '')


def allowed_file(f):
    return (f and f.filename and
            '.' in f.filename and
            f.filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS)


def save_file(f, subfolder):
    if not allowed_file(f):
        return None
    ts = datetime.now().strftime('%Y%m%d_%H%M%S_')
    filename = ts + secure_filename(f.filename)
    folder = os.path.join(app.config['UPLOAD_FOLDER'], subfolder)
    os.makedirs(folder, exist_ok=True)
    f.save(os.path.join(folder, filename))
    return f'uploads/{subfolder}/{filename}'


def parse_dt(s):
    if not s:
        return None
    for fmt in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.strptime(str(s), fmt)
        except ValueError:
            continue
    return None


def portero_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('portero'):
            return redirect(url_for('portero_login'))
        return f(*args, **kwargs)
    return decorated


def validate_doc_date(file_path, manual_date_str, doc_label):
    """Extrae la fecha del documento con IA y la cruza con la ingresada manualmente.
    Retorna (fecha_final, error_o_None).
    - Si IA lee y manual difiere → error, bloquea el envío.
    - Si IA lee → usa esa fecha (ignora manual).
    - Si IA falla → usa fecha manual.
    """
    ai_date = extract_expiration_date(file_path)
    manual = (manual_date_str or '').strip()
    if ai_date and manual:
        try:
            ai_dt = datetime.strptime(ai_date, '%Y-%m-%d').date()
            man_dt = datetime.strptime(manual, '%Y-%m-%d').date()
            if ai_dt != man_dt:
                return None, (
                    f'{doc_label}: la fecha ingresada ({man_dt.strftime("%d/%m/%Y")}) '
                    f'no coincide con la del documento ({ai_dt.strftime("%d/%m/%Y")}). '
                    f'Corregí la fecha e intentá de nuevo.'
                )
        except ValueError:
            pass
    return ai_date or manual or None, None


def extract_expiration_date(relative_path):
    """Lee la fecha de vencimiento directamente de una imagen usando Claude Vision.
    Devuelve string YYYY-MM-DD o None si falla o no aplica (PDF, sin API key, etc.)."""
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not relative_path or not api_key:
        return None
    ext = relative_path.rsplit('.', 1)[-1].lower()
    if ext not in ('jpg', 'jpeg', 'png'):
        return None
    try:
        import anthropic
        import base64
        full_path = os.path.join(os.path.dirname(__file__), 'static', relative_path)
        if not os.path.exists(full_path):
            return None
        media_type = 'image/jpeg' if ext in ('jpg', 'jpeg') else 'image/png'
        with open(full_path, 'rb') as fimg:
            image_data = base64.standard_b64encode(fimg.read()).decode('utf-8')
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model='claude-opus-4-8',
            max_tokens=50,
            messages=[{
                'role': 'user',
                'content': [
                    {
                        'type': 'image',
                        'source': {
                            'type': 'base64',
                            'media_type': media_type,
                            'data': image_data,
                        }
                    },
                    {
                        'type': 'text',
                        'text': (
                            'Este es un documento argentino (carnet de conducir, '
                            'póliza de seguro de vehículo u otro documento con vencimiento). '
                            'Encontrá la fecha de vencimiento (puede decir "VTO", "Vence", '
                            '"Válido hasta", "Fecha de vencimiento", etc.) y respondé SOLO '
                            'con la fecha en formato YYYY-MM-DD (ejemplo: 2026-03-15). '
                            'Si no podés determinarlo con certeza, respondé exactamente: NO_ENCONTRADA'
                        )
                    }
                ]
            }]
        )
        result = msg.content[0].text.strip()
        if result == 'NO_ENCONTRADA':
            return None
        datetime.strptime(result, '%Y-%m-%d')
        return result
    except Exception:
        return None


def extract_dnis_from_nomina(relative_path):
    """Lee la nómina del 931 (TXT, PDF o imagen) y devuelve un set de DNIs (8 dígitos, zero-padded)."""
    if not relative_path:
        return set()
    full_path = os.path.join(os.path.dirname(__file__), 'static', relative_path)
    if not os.path.exists(full_path):
        return set()
    ext = relative_path.rsplit('.', 1)[-1].lower()

    text = ''
    if ext == 'txt':
        try:
            with open(full_path, 'r', encoding='latin-1', errors='replace') as f:
                text = f.read()
        except Exception:
            return set()
    elif ext == 'pdf':
        try:
            import pdfplumber
            with pdfplumber.open(full_path) as pdf:
                text = '\n'.join(page.extract_text() or '' for page in pdf.pages)
        except Exception:
            return set()
    elif ext in ('jpg', 'jpeg', 'png'):
        api_key = os.environ.get('ANTHROPIC_API_KEY')
        if not api_key:
            return set()
        try:
            import anthropic
            import base64
            media_type = 'image/jpeg' if ext in ('jpg', 'jpeg') else 'image/png'
            with open(full_path, 'rb') as f:
                image_data = base64.standard_b64encode(f.read()).decode('utf-8')
            client = anthropic.Anthropic(api_key=api_key)
            msg = client.messages.create(
                model='claude-haiku-4-5-20251001',
                max_tokens=1024,
                messages=[{
                    'role': 'user',
                    'content': [
                        {'type': 'image', 'source': {'type': 'base64',
                                                      'media_type': media_type,
                                                      'data': image_data}},
                        {'type': 'text', 'text': (
                            'Este es un documento de nómina de AFIP Argentina. '
                            'Extraé todos los números de CUIL que aparezcan '
                            '(formato XX-XXXXXXXX-X o 11 dígitos seguidos). '
                            'Respondé SOLO con los CUILes separados por comas, '
                            'sin texto adicional.'
                        )}
                    ]
                }]
            )
            text = msg.content[0].text
        except Exception:
            return set()

    return _parse_dnis_from_text(text)


def _parse_dnis_from_text(text):
    """Extrae DNIs de un texto buscando patrones CUIL (XX-XXXXXXXX-X o 11 dígitos)."""
    dnis = set()
    for m in re.finditer(r'\b(\d{2})-?(\d{8})-?(\d)\b', text):
        dnis.add(m.group(2))
    return dnis


def _dni_en_nomina(emp_dni, nomina_dnis):
    """Compara ignorando ceros a la izquierda."""
    try:
        emp_int = int(emp_dni)
        return any(int(d) == emp_int for d in nomina_dnis)
    except (ValueError, TypeError):
        return False


# ── home ─────────────────────────────────────────────────────────────────────

@app.route('/')
def home():
    return render_template('home.html')


# ── visitas ──────────────────────────────────────────────────────────────────

@app.route('/visita', methods=['GET', 'POST'])
def visita():
    if request.method == 'POST':
        nombre = request.form['nombre'].strip()
        apellido = request.form['apellido'].strip()
        dni = normalizar_dni(request.form.get('dni'))
        es_conductor = request.form.get('es_conductor') == 'si'

        patente = seguro_path = carnet_path = None
        carnet_vencimiento = seguro_vencimiento = None

        if es_conductor:
            patente = request.form.get('patente', '').strip() or None
            seguro_path = save_file(request.files.get('seguro_vehiculo'), 'seguros')
            carnet_path = save_file(request.files.get('carnet_conducir'), 'carnets')
            carnet_vencimiento, err_carnet = validate_doc_date(
                carnet_path,
                request.form.get('carnet_vencimiento', ''),
                'Carnet de conducir',
            )
            seguro_vencimiento, err_seguro = validate_doc_date(
                seguro_path,
                request.form.get('seguro_vencimiento', ''),
                'Seguro del vehículo',
            )
            errores = [e for e in [err_carnet, err_seguro] if e]
            if errores:
                for e in errores:
                    flash(e)
                today = date.today()
                return render_template('visita.html', meses=MESES,
                                       anos=list(range(today.year, today.year + 10)))

        now = datetime.now().isoformat(sep=' ')
        db = get_db()
        db.execute(
            '''INSERT INTO visitas
               (nombre, apellido, dni, es_conductor, patente,
                seguro_vehiculo_path, carnet_conducir_path,
                carnet_vencimiento, seguro_vencimiento, fecha_registro)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(dni) DO UPDATE SET
                 nombre=excluded.nombre, apellido=excluded.apellido,
                 es_conductor=excluded.es_conductor, patente=excluded.patente,
                 seguro_vehiculo_path=excluded.seguro_vehiculo_path,
                 carnet_conducir_path=excluded.carnet_conducir_path,
                 carnet_vencimiento=excluded.carnet_vencimiento,
                 seguro_vencimiento=excluded.seguro_vencimiento,
                 fecha_registro=excluded.fecha_registro''',
            (nombre, apellido, dni, int(es_conductor), patente,
             seguro_path, carnet_path, carnet_vencimiento, seguro_vencimiento, now)
        )
        db.commit()
        db.close()
        return redirect(url_for('visita_ok'))

    today = date.today()
    return render_template('visita.html', meses=MESES,
                           anos=list(range(today.year, today.year + 10)))


@app.route('/visita/ok')
def visita_ok():
    return render_template('visita_ok.html')


# ── prestadores ──────────────────────────────────────────────────────────────

@app.route('/prestador', methods=['GET', 'POST'])
def prestador():
    if request.method == 'POST':
        razon_social = request.form['razon_social'].strip()
        cuit = normalizar_dni(request.form.get('cuit'))
        categoria = request.form['categoria']
        gmail = request.form.get('gmail', '').strip()

        resp_nombre = request.form.get('resp_nombre', '').strip() or None
        resp_dni = normalizar_dni(request.form.get('resp_dni')) or None
        resp_maneja = int(request.form.get('resp_maneja') == 'si')
        resp_carnet_path = None
        resp_carnet_venc = None
        errores = []

        f931_empresa = request.files.get('formulario_931')
        formulario_931_path = save_file(f931_empresa, 'formularios_931') if f931_empresa and f931_empresa.filename else None

        if resp_maneja:
            resp_carnet_path = save_file(request.files.get('resp_carnet'), 'carnets_resp')
            resp_carnet_venc, err = validate_doc_date(
                resp_carnet_path,
                request.form.get('resp_carnet_vencimiento', ''),
                'Carnet del responsable',
            )
            if err:
                errores.append(err)

        nombres_emp = request.form.getlist('emp_nombre[]')
        dnis_emp = request.form.getlist('emp_dni[]')
        files_carnet_emp = request.files.getlist('emp_carnet[]')
        carnet_venc_list = request.form.getlist('emp_carnet_venc[]')

        emp_data = []
        for i, (enombre, edni) in enumerate(zip(nombres_emp, dnis_emp)):
            enombre = enombre.strip()
            edni = normalizar_dni(edni)
            if not enombre or not edni:
                continue
            fcarnet = files_carnet_emp[i] if i < len(files_carnet_emp) else None
            manual_venc = carnet_venc_list[i].strip() if i < len(carnet_venc_list) else ''
            carnet_path_emp = save_file(fcarnet, 'carnets_emp')
            emp_carnet_venc_final, err = validate_doc_date(
                carnet_path_emp,
                manual_venc,
                f'Carnet de empleado {enombre}',
            )
            if err:
                errores.append(err)
            emp_data.append((enombre, edni, carnet_path_emp, emp_carnet_venc_final))

        patentes = request.form.getlist('veh_patente[]')
        vencimientos = request.form.getlist('veh_vencimiento[]')
        files_seguro = request.files.getlist('veh_seguro[]')

        veh_data = []
        for i, patente in enumerate(patentes):
            patente = patente.strip().upper()
            if not patente:
                continue
            venc_manual = vencimientos[i].strip() if i < len(vencimientos) else ''
            fseguro = files_seguro[i] if i < len(files_seguro) else None
            seguro_path_veh = save_file(fseguro, 'seguros_vehiculos')
            venc_final, err = validate_doc_date(
                seguro_path_veh,
                venc_manual,
                f'Seguro del vehículo {patente}',
            )
            if err:
                errores.append(err)
            veh_data.append((patente, seguro_path_veh, venc_final))

        f_nomina = request.files.get('nomina_931')
        nomina_path = save_file(f_nomina, 'nominas') if f_nomina and f_nomina.filename else None
        if emp_data and not nomina_path:
            errores.append('Si declarás empleados, la nómina del 931 es obligatoria.')

        if errores:
            for e in errores:
                flash(e)
            return render_template('prestador.html')

        # Verificar que cada empleado figure en la nómina
        dnis_nomina = extract_dnis_from_nomina(nomina_path)
        if dnis_nomina and emp_data:
            no_encontrados = [
                f'{enombre} (DNI {edni})'
                for enombre, edni, *_ in emp_data
                if not _dni_en_nomina(edni, dnis_nomina)
            ]
            if no_encontrados:
                for nombre in no_encontrados:
                    flash(f'El empleado {nombre} no figura en la nómina del 931.')
                return render_template('prestador.html')

        now = datetime.now().isoformat(sep=' ')
        db = get_db()
        existing = db.execute(
            'SELECT id FROM prestadores WHERE cuit = ?', (cuit,)
        ).fetchone()

        if existing:
            prestador_id = existing['id']
            db.execute(
                '''UPDATE prestadores
                   SET razon_social=?, categoria_tributaria=?, gmail=?,
                       resp_nombre=?, resp_dni=?, resp_maneja=?,
                       resp_carnet_path=?, resp_carnet_vencimiento=?,
                       formulario_931_path=?, nomina_path=?, fecha_registro=?
                   WHERE id=?''',
                (razon_social, categoria, gmail,
                 resp_nombre, resp_dni, resp_maneja,
                 resp_carnet_path, resp_carnet_venc,
                 formulario_931_path, nomina_path, now, prestador_id)
            )
            db.execute('DELETE FROM empleados WHERE prestador_id = ?', (prestador_id,))
            db.execute('DELETE FROM vehiculos WHERE prestador_id = ?', (prestador_id,))
        else:
            cur = db.execute(
                '''INSERT INTO prestadores
                   (razon_social, cuit, categoria_tributaria, gmail,
                    resp_nombre, resp_dni, resp_maneja,
                    resp_carnet_path, resp_carnet_vencimiento,
                    formulario_931_path, nomina_path, fecha_registro)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                (razon_social, cuit, categoria, gmail,
                 resp_nombre, resp_dni, resp_maneja,
                 resp_carnet_path, resp_carnet_venc,
                 formulario_931_path, nomina_path, now)
            )
            prestador_id = cur.lastrowid

        for enombre, edni, carnet_path_emp, emp_carnet_venc_final in emp_data:
            db.execute(
                '''INSERT INTO empleados
                   (prestador_id, nombre, dni, formulario_931_path,
                    carnet_conducir_path, carnet_vencimiento)
                   VALUES (?,?,?,?,?,?)''',
                (prestador_id, enombre, edni,
                 None,
                 carnet_path_emp,
                 emp_carnet_venc_final)
            )

        for patente, seguro_path_veh, venc_final in veh_data:
            db.execute(
                '''INSERT INTO vehiculos
                   (prestador_id, patente, seguro_path, seguro_vencimiento)
                   VALUES (?,?,?,?)''',
                (prestador_id, patente, seguro_path_veh, venc_final)
            )

        db.commit()
        db.close()
        return redirect(url_for('prestador_ok'))

    return render_template('prestador.html')


@app.route('/prestador/ok')
def prestador_ok():
    return render_template('prestador_ok.html')


# ── portero ──────────────────────────────────────────────────────────────────

@app.route('/portero/login', methods=['GET', 'POST'])
def portero_login():
    if request.method == 'POST':
        if request.form.get('password') == PORTERO_PASSWORD:
            session['portero'] = True
            return redirect(url_for('portero'))
        flash('Contraseña incorrecta.')
    return render_template('portero_login.html')


@app.route('/portero/logout')
def portero_logout():
    session.pop('portero', None)
    return redirect(url_for('portero_login'))


@app.route('/portero')
@portero_required
def portero():
    return render_template('portero.html')


@app.route('/portero/buscar', methods=['POST'])
@portero_required
def portero_buscar():
    dni = normalizar_dni(request.form.get('dni'))
    today = date.today()
    resultados = []
    db = get_db()

    # ── visita por DNI ──
    row = db.execute('SELECT * FROM visitas WHERE dni = ?', (dni,)).fetchone()
    if row:
        problemas = []
        if row['es_conductor']:
            venc_carnet = parse_dt(row['carnet_vencimiento'])
            if venc_carnet:
                if venc_carnet.date() < today:
                    problemas.append(
                        f'Carnet de conducir vencido '
                        f'(venció {venc_carnet.strftime("%d/%m/%Y")})'
                    )
            else:
                problemas.append('Falta fecha de vencimiento del carnet de conducir')
        resultados.append({
            'tipo': 'Visita',
            'nombre': f"{row['nombre']} {row['apellido']}",
            'empresa': None,
            'apto': not problemas,
            'problemas': problemas,
        })

    # ── empleado de prestador por DNI ──
    emp = db.execute(
        '''SELECT e.nombre AS emp_nombre, e.prestador_id,
                  e.carnet_vencimiento AS emp_carnet_venc,
                  p.razon_social, p.fecha_registro AS prest_fecha
           FROM empleados e
           JOIN prestadores p ON e.prestador_id = p.id
           WHERE e.dni = ?
           ORDER BY p.fecha_registro DESC
           LIMIT 1''',
        (dni,)
    ).fetchone()

    if emp:
        problemas = []
        pdt = parse_dt(emp['prest_fecha'])
        if pdt and (today - pdt.date()).days > 40:
            problemas.append(
                f'Formulario 931 vencido '
                f'(último envío: {pdt.strftime("%d/%m/%Y")}, hace {(today - pdt.date()).days} días)'
            )
        if emp['emp_carnet_venc']:
            venc_carnet = parse_dt(emp['emp_carnet_venc'])
            if venc_carnet and venc_carnet.date() < today:
                problemas.append(
                    f'Carnet de conducir vencido '
                    f'(venció {venc_carnet.strftime("%d/%m/%Y")})'
                )
        resultados.append({
            'tipo': 'Empleado de Prestador',
            'nombre': emp['emp_nombre'],
            'empresa': emp['razon_social'],
            'apto': not problemas,
            'problemas': problemas,
        })

    # ── prestador por CUIT ──
    prest = db.execute(
        'SELECT * FROM prestadores WHERE cuit = ?', (dni,)
    ).fetchone()
    if prest:
        problemas = []
        pdt = parse_dt(prest['fecha_registro'])
        if pdt and (today - pdt.date()).days > 40:
            problemas.append(
                f'Formulario 931 vencido '
                f'(último envío: {pdt.strftime("%d/%m/%Y")}, hace {(today - pdt.date()).days} días)'
            )
        if prest['resp_maneja'] and prest['resp_carnet_vencimiento']:
            venc_carnet = parse_dt(prest['resp_carnet_vencimiento'])
            if venc_carnet and venc_carnet.date() < today:
                problemas.append(
                    f'Carnet del responsable vencido '
                    f'(venció {venc_carnet.strftime("%d/%m/%Y")})'
                )
        elif prest['resp_maneja'] and not prest['resp_carnet_vencimiento']:
            problemas.append('Falta fecha de vencimiento del carnet del responsable')
        resultados.append({
            'tipo': 'Prestador de Servicio',
            'nombre': prest['resp_nombre'] or prest['razon_social'],
            'empresa': prest['razon_social'],
            'apto': not problemas,
            'problemas': problemas,
        })

    db.close()

    if not resultados:
        resultados.append({
            'tipo': None,
            'nombre': None,
            'empresa': None,
            'apto': False,
            'problemas': ['DNI o CUIT no registrado en el sistema'],
        })

    return render_template('portero_resultado.html',
                           resultados=resultados, dni=dni)


@app.route('/portero/buscar-patente', methods=['POST'])
@portero_required
def portero_buscar_patente():
    patente = request.form.get('patente', '').strip().upper()
    today = date.today()
    resultados = []
    db = get_db()

    # ── vehículo de visita ──
    row = db.execute(
        'SELECT * FROM visitas WHERE patente = ?', (patente,)
    ).fetchone()
    if row:
        problemas = []
        venc = parse_dt(row['seguro_vencimiento'])
        if venc:
            if venc.date() < today:
                problemas.append(f'Seguro vencido (venció {venc.strftime("%d/%m/%Y")})')
        else:
            problemas.append('Falta fecha de vencimiento del seguro')
        resultados.append({
            'tipo': 'Vehículo de Visita',
            'patente': patente,
            'titular': f"{row['nombre']} {row['apellido']}",
            'empresa': None,
            'apto': not problemas,
            'problemas': problemas,
        })

    # ── vehículo de prestador ──
    veh = db.execute(
        '''SELECT v.*, p.razon_social
           FROM vehiculos v
           JOIN prestadores p ON v.prestador_id = p.id
           WHERE v.patente = ?''',
        (patente,)
    ).fetchone()
    if veh:
        problemas = []
        venc = parse_dt(veh['seguro_vencimiento'])
        if venc:
            if venc.date() < today:
                problemas.append(f'Seguro vencido (venció {venc.strftime("%d/%m/%Y")})')
        else:
            problemas.append('Falta fecha de vencimiento del seguro')
        resultados.append({
            'tipo': 'Vehículo de Prestador',
            'patente': patente,
            'titular': None,
            'empresa': veh['razon_social'],
            'apto': not problemas,
            'problemas': problemas,
        })

    db.close()

    if not resultados:
        resultados.append({
            'tipo': None,
            'patente': patente,
            'titular': None,
            'empresa': None,
            'apto': False,
            'problemas': ['Patente no registrada en el sistema'],
        })

    return render_template('portero_resultado_patente.html',
                           resultados=resultados, patente=patente)


# ── registrar ingreso ─────────────────────────────────────────────────────────

@app.route('/portero/registrar_ingreso', methods=['POST'])
@portero_required
def registrar_ingreso():
    dni     = request.form.get('dni', '').strip()
    nombre  = request.form.get('nombre', '').strip()
    tipo    = request.form.get('tipo', '').strip()
    empresa = request.form.get('empresa', '').strip() or None
    now     = datetime.now().isoformat(sep=' ')
    db = get_db()
    db.execute(
        'INSERT INTO ingresos (dni, nombre, tipo, empresa, fecha_ingreso) VALUES (?,?,?,?,?)',
        (dni, nombre, tipo, empresa, now)
    )
    db.commit()
    db.close()
    flash(f'Ingreso de {nombre} registrado correctamente.')
    return redirect(url_for('portero'))


# ── admin ─────────────────────────────────────────────────────────────────────

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated


@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        if request.form.get('password') == PORTERO_PASSWORD:
            session['admin'] = True
            return redirect(url_for('admin_panel'))
        flash('Contraseña incorrecta.')
    return render_template('admin_login.html')


@app.route('/admin/logout')
def admin_logout():
    session.pop('admin', None)
    return redirect(url_for('admin_login'))


@app.route('/admin')
@admin_required
def admin_panel():
    today = date.today()
    db = get_db()

    # Último ingreso por DNI
    ultimos = {
        row['dni']: row['fecha_ingreso']
        for row in db.execute(
            '''SELECT dni, MAX(fecha_ingreso) AS fecha_ingreso
               FROM ingresos GROUP BY dni'''
        ).fetchall()
    }

    personas = []

    # Visitantes
    for row in db.execute('SELECT * FROM visitas ORDER BY fecha_registro DESC').fetchall():
        problemas = []
        if row['es_conductor']:
            venc = parse_dt(row['carnet_vencimiento'])
            if venc:
                if venc.date() < today:
                    problemas.append(f'Carnet vencido ({venc.strftime("%d/%m/%Y")})')
            else:
                problemas.append('Sin fecha de carnet')
            venc_s = parse_dt(row['seguro_vencimiento'])
            if venc_s:
                if venc_s.date() < today:
                    problemas.append(f'Seguro vencido ({venc_s.strftime("%d/%m/%Y")})')
            else:
                problemas.append('Sin fecha de seguro')
        personas.append({
            'nombre':         f"{row['nombre']} {row['apellido']}",
            'dni':            row['dni'],
            'tipo':           'Visita',
            'empresa':        '—',
            'apto':           not problemas,
            'problemas':      problemas,
            'fecha_registro': row['fecha_registro'],
            'ultimo_ingreso': ultimos.get(row['dni']),
        })

    # Empleados de prestadores
    for row in db.execute(
        '''SELECT e.nombre, e.dni, e.carnet_vencimiento,
                  p.razon_social, p.fecha_registro AS prest_fecha
           FROM empleados e
           JOIN prestadores p ON e.prestador_id = p.id
           ORDER BY p.fecha_registro DESC'''
    ).fetchall():
        problemas = []
        pdt = parse_dt(row['prest_fecha'])
        if pdt and (today - pdt.date()).days > 40:
            problemas.append(f'931 vencido (hace {(today - pdt.date()).days} días)')
        if row['carnet_vencimiento']:
            venc = parse_dt(row['carnet_vencimiento'])
            if venc and venc.date() < today:
                problemas.append(f'Carnet vencido ({venc.strftime("%d/%m/%Y")})')
        personas.append({
            'nombre':         row['nombre'],
            'dni':            row['dni'],
            'tipo':           'Empleado',
            'empresa':        row['razon_social'],
            'apto':           not problemas,
            'problemas':      problemas,
            'fecha_registro': row['prest_fecha'],
            'ultimo_ingreso': ultimos.get(row['dni']),
        })

    db.close()

    personas.sort(key=lambda x: x['fecha_registro'] or '', reverse=True)

    return render_template('admin.html', personas=personas)


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
