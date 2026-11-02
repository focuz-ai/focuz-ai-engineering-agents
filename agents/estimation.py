"""Estimador local: dos gates humanos persistentes y precio determinista; sin LLM."""
from contextlib import contextmanager
import argparse
from decimal import (Context, Decimal, DivisionByZero, InvalidOperation,
                     Overflow, ROUND_HALF_UP, localcontext)
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from .core import Principal


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def digest(value):
    return sha256(canonical(value).encode()).hexdigest()


def money_context():
    # <=30 tareas y <=64 decimales por entrada; <210 dígitos para el producto
    # exacto de suma de horas, tarifa y factor. No heredar estado Decimal global.
    return Context(prec=256, rounding=ROUND_HALF_UP, Emin=-1000, Emax=1000,
                   capitals=1, clamp=0, flags=[],
                   traps=[InvalidOperation, DivisionByZero, Overflow])


def decimal_value(value, minimum=Decimal('0'), maximum=Decimal('1000000'), allow_zero=False):
    if type(value) not in (str, int, Decimal):
        raise ValueError('Se requiere decimal textual o entero')
    try:
        number = Decimal(value)
        if (not number.is_finite() or number > maximum or number < minimum
                or (not allow_zero and number == minimum)):
            raise ValueError('Decimal fuera de rango')
        if max(-number.as_tuple().exponent, 0) > 64:
            raise ValueError('Se permiten como máximo 64 decimales')
        return number
    except InvalidOperation:
        raise ValueError('Decimal inválido') from None


class EstimationFlow:
    def __init__(self, path=':memory:'):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute('''CREATE TABLE IF NOT EXISTS estimation_flows(
            id TEXT PRIMARY KEY, tenant TEXT NOT NULL, author TEXT NOT NULL,
            plan TEXT NOT NULL, plan_digest TEXT NOT NULL, state TEXT NOT NULL,
            reviewer1 TEXT, estimator TEXT, estimate TEXT, estimate_digest TEXT,
            final_decision TEXT, result TEXT)''')
        self.db.commit()

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def row(self, request_id, principal):
        row = self.db.execute('SELECT * FROM estimation_flows WHERE id=?', (request_id,)).fetchone()
        if row is None or row['tenant'] != principal.tenant:
            raise PermissionError('Flujo no accesible')
        return row

    def propose(self, request_id, principal, tasks, hourly_rate, contingency_percent='0'):
        if 'support' not in principal.roles:
            raise PermissionError('No puede proponer')
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 100:
            raise ValueError('ID inválido')
        if not isinstance(tasks, list) or not 1 <= len(tasks) <= 30:
            raise ValueError('Se requieren de 1 a 30 tareas')
        for task in tasks:
            if (not isinstance(task, dict) or set(task) != {'id', 'description'}
                    or any(not isinstance(task[k], str) or not task[k].strip() for k in task)
                    or len(task['id']) > 50 or len(task['description']) > 500):
                raise ValueError('Desglose inválido')
        if len({t['id'] for t in tasks}) != len(tasks):
            raise ValueError('ID de tarea duplicado')
        rate = decimal_value(hourly_rate)
        contingency = decimal_value(contingency_percent, maximum=Decimal('100'), allow_zero=True)
        plan = {'tasks': tasks, 'hourly_rate': str(rate), 'contingency_percent': str(contingency)}
        plan_digest = digest(plan)
        with self.transaction():
            existing = self.db.execute('SELECT * FROM estimation_flows WHERE id=?', (request_id,)).fetchone()
            if existing:
                if (existing['tenant'], existing['author'], existing['plan_digest']) != (principal.tenant, principal.user, plan_digest):
                    raise ValueError('ID reutilizado con otro ámbito o desglose')
            else:
                self.db.execute('INSERT INTO estimation_flows(id,tenant,author,plan,plan_digest,state) VALUES (?,?,?,?,?,?)',
                                (request_id, principal.tenant, principal.user, canonical(plan), plan_digest, 'BREAKDOWN_PENDING'))
        return {'request_id': request_id, 'plan_digest': plan_digest}

    def approve_breakdown(self, request_id, principal, expected_digest):
        if 'approver' not in principal.roles:
            raise PermissionError('No puede aprobar')
        with self.transaction():
            row = self.row(request_id, principal)
            if row['author'] == principal.user:
                raise PermissionError('El autor no puede aprobar su desglose')
            if row['plan_digest'] != expected_digest:
                raise ValueError('Digest del desglose no coincide')
            if row['state'] not in ('BREAKDOWN_PENDING', 'ESTIMATION_PENDING'):
                raise ValueError('Estado incompatible con gate 1')
            self.db.execute('UPDATE estimation_flows SET state=?,reviewer1=? WHERE id=?',
                            ('ESTIMATION_PENDING', principal.user, request_id))

    def submit_estimates(self, request_id, principal, expected_plan_digest, estimates):
        if 'estimator' not in principal.roles:
            raise PermissionError('No puede aportar estimaciones')
        with self.transaction():
            row = self.row(request_id, principal)
            if row['state'] not in ('ESTIMATION_PENDING', 'FINAL_PENDING') or row['plan_digest'] != expected_plan_digest:
                raise PermissionError('Falta gate 1 o cambió el desglose')
            tasks = json.loads(row['plan'])['tasks']
            if not isinstance(estimates, list) or len(estimates) != len(tasks):
                raise ValueError('La estimación debe cubrir exactamente el desglose')
            normalized = []
            for item in estimates:
                if (not isinstance(item, dict) or set(item) != {'id', 'hours', 'evidence'}
                        or not isinstance(item['id'], str) or not isinstance(item['evidence'], list)
                        or len(item['evidence']) > 10
                        or any(not isinstance(e, str) or not e.strip() or len(e) > 100 for e in item['evidence'])):
                    raise ValueError('Estimación inválida')
                if item['hours'] is None:
                    if item['evidence']:
                        raise ValueError('Una abstención debe identificarse sin precedentes')
                    hours = None
                else:
                    if not item['evidence']:
                        raise ValueError('Horas automáticas sin evidencia')
                    hours = str(decimal_value(item['hours'], maximum=Decimal('100000')))
                normalized.append({'id': item['id'], 'hours': hours, 'evidence': item['evidence']})
            if len({item['id'] for item in normalized}) != len(tasks) or {i['id'] for i in normalized} != {t['id'] for t in tasks}:
                raise ValueError('IDs de tareas distintos o duplicados')
            normalized.sort(key=lambda item: item['id'])
            payload = {'plan_digest': row['plan_digest'], 'estimates': normalized}
            estimate_digest = digest(payload)
            self.db.execute('UPDATE estimation_flows SET state=?,estimator=?,estimate=?,estimate_digest=? WHERE id=?',
                            ('FINAL_PENDING', principal.user, canonical(payload), estimate_digest, request_id))
        return {'request_id': request_id, 'estimate_digest': estimate_digest}

    def approve_final(self, request_id, principal, expected_digest, hours_overrides=None):
        if 'approver' not in principal.roles:
            raise PermissionError('No puede aprobar')
        if hours_overrides is None:
            hours_overrides = {}
        if not isinstance(hours_overrides, dict) or any(not isinstance(key, str) for key in hours_overrides):
            raise ValueError('Correcciones inválidas')
        overrides = {key: str(decimal_value(value, maximum=Decimal('100000'))) for key, value in hours_overrides.items()}
        decision = canonical({'estimate_digest': expected_digest, 'overrides': overrides, 'reviewer': principal.user})
        with self.transaction():
            row = self.row(request_id, principal)
            if row['author'] == principal.user or row['estimator'] == principal.user:
                raise PermissionError('Se requiere revisión independiente del autor y estimador')
            if row['estimate_digest'] != expected_digest:
                raise ValueError('Digest de estimación no coincide')
            if row['state'] == 'DONE':
                if row['final_decision'] != decision:
                    raise ValueError('Un resultado final no admite otra decisión')
                return json.loads(row['result'])
            if row['state'] != 'FINAL_PENDING':
                raise PermissionError('Falta la estimación y gate 2')
            plan = json.loads(row['plan'])
            estimates = json.loads(row['estimate'])['estimates']
            if not set(overrides) <= {item['id'] for item in estimates}:
                raise ValueError('Corrección de una tarea inexistente')
            lines = []
            for item in estimates:
                hours = overrides.get(item['id'], item['hours'])
                if hours is None:
                    raise ValueError('Faltan horas revisadas para una tarea sin precedente')
                lines.append(dict(item, hours=hours, hours_origin='human' if item['id'] in overrides else 'historical_fixture'))
            with localcontext(money_context()):
                total_hours = sum(Decimal(item['hours']) for item in lines)
                cost = total_hours * Decimal(plan['hourly_rate']) * (1 + Decimal(plan['contingency_percent']) / 100)
                amount = str(cost.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
            result = {'request_id': request_id, 'state': 'DONE', 'hours': str(total_hours),
                      'amount': amount,
                      'currency': 'EUR', 'lines': lines, 'reviewer': principal.user,
                      'mode': 'local_simulation', 'commercial_proposal_sent': False}
            self.db.execute('UPDATE estimation_flows SET state=?,final_decision=?,result=? WHERE id=?',
                            ('DONE', decision, canonical(result), request_id))
            return result


def read_workshop_exchange(path, trusted_tenant):
    """No acepta identidad del fichero. Solo la fixture declarada, acotada a 100 KiB."""
    with Path(path).open('rb') as stream:
        raw = stream.read(102401)
    if len(raw) > 102400:
        raise ValueError('Intercambio demasiado grande')
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError('Intercambio JSON inválido') from None
    if (not isinstance(value, dict) or set(value) != {'schema_version', 'tenant', 'tasks', 'estimates', 'data_kind'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['data_kind'] != 'synthetic_workshop_fixture'):
        raise ValueError('Contrato de intercambio no admitido')
    if value['tenant'] != trusted_tenant:
        raise PermissionError('Tenant del intercambio distinto del ámbito autorizado')
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, help='JSON sintético creado por rag.estimation --export')
    args = parser.parse_args()
    data = read_workshop_exchange(args.input, 'A') if args.input else {
        'tasks': [{'id': 'auth', 'description': 'Autenticación login usuarios'},
                  {'id': 'novel', 'description': 'Adaptador sin precedente'}],
        'estimates': [{'id': 'auth', 'hours': '10', 'evidence': ['A-auth-1', 'A-auth-2']},
                      {'id': 'novel', 'hours': None, 'evidence': []}]}
    flow = EstimationFlow()
    try:
        author = Principal('A', 'ana', ('support',))
        reviewer = Principal('A', 'bea', ('approver',))
        estimator = Principal('A', 'retriever', ('estimator',))
        proposal = flow.propose('estimate-demo-1', author, data['tasks'], '60', '10')
        flow.approve_breakdown('estimate-demo-1', reviewer, proposal['plan_digest'])
        estimates = flow.submit_estimates('estimate-demo-1', estimator, proposal['plan_digest'], data['estimates'])
        result = flow.approve_final('estimate-demo-1', reviewer, estimates['estimate_digest'], {'novel': '4'})
        print(json.dumps(result, ensure_ascii=False))
        repeated = flow.approve_final('estimate-demo-1', reviewer, estimates['estimate_digest'], {'novel': '4'})
        print(json.dumps({'repeat_equal': repeated == result}))
    finally:
        flow.close()


if __name__ == '__main__':
    main()
