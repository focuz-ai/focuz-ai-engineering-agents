"""Workflow local persistente. Las propuestas no conceden permisos."""
from dataclasses import dataclass
from hashlib import sha256
import json
import sqlite3

@dataclass(frozen=True)
class Principal:
    tenant: str
    user: str
    roles: tuple[str,...]

class Workflow:
    def __init__(self, path=':memory:'):
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS requests(
          id TEXT PRIMARY KEY, tenant TEXT NOT NULL, author TEXT NOT NULL,
          payload TEXT NOT NULL, digest TEXT NOT NULL, state TEXT NOT NULL,
          approver TEXT, result TEXT);
        CREATE TABLE IF NOT EXISTS tickets(
          id INTEGER PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
          tenant TEXT NOT NULL, summary TEXT NOT NULL,
          FOREIGN KEY(request_id) REFERENCES requests(id));
        """)
    def close(self): self.db.close()

    def propose(self, request_id, principal, tool, summary):
        if 'support' not in principal.roles: raise PermissionError('No puede proponer')
        if tool != 'create_local_ticket': raise ValueError('Herramienta fuera de allowlist')
        if not request_id or not summary.strip() or len(summary)>300:
            raise ValueError('ID o resumen inválido')
        payload = json.dumps({'tool':tool,'summary':summary},sort_keys=True,ensure_ascii=False)
        digest = sha256(payload.encode()).hexdigest()
        with self.db:
            row = self.db.execute('SELECT tenant,author,digest,state FROM requests WHERE id=?',
                                  (request_id,)).fetchone()
            if row:
                if row[:3] != (principal.tenant,principal.user,digest):
                    raise ValueError('ID reutilizado con otro ámbito o payload')
                return {'request_id':request_id,'digest':digest,'state':row[3]}
            self.db.execute('INSERT INTO requests VALUES (?,?,?,?,?,?,NULL,NULL)',
              (request_id,principal.tenant,principal.user,payload,digest,'PENDING'))
        return {'request_id':request_id,'digest':digest,'state':'PENDING'}

    def _row(self, request_id, principal):
        row = self.db.execute('SELECT tenant,author,payload,digest,state,approver,result FROM requests WHERE id=?',
                              (request_id,)).fetchone()
        if not row or row[0] != principal.tenant: raise PermissionError('Solicitud no accesible')
        return row

    def approve(self, request_id, principal, expected_digest):
        if 'approver' not in principal.roles: raise PermissionError('No puede aprobar')
        with self.db:
            row = self._row(request_id,principal)
            if row[1] == principal.user: raise PermissionError('Requiere otro aprobador')
            if row[3] != expected_digest: raise ValueError('Aprobación no coincide con payload')
            if row[4] not in ('PENDING','APPROVED'): raise ValueError('Estado no aprobable')
            self.db.execute('UPDATE requests SET state=?,approver=? WHERE id=?',
                            ('APPROVED',principal.user,request_id))

    def execute(self, request_id, principal):
        if 'executor' not in principal.roles: raise PermissionError('No puede ejecutar')
        # BEGIN IMMEDIATE serializa la transición y el efecto en ESTA base SQLite.
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row = self._row(request_id,principal)
            if row[4] == 'DONE':
                self.db.commit()
                return json.loads(row[6])
            if row[4] != 'APPROVED': raise PermissionError('Falta aprobación humana')
            obj = json.loads(row[2])
            if obj['tool'] != 'create_local_ticket': raise ValueError('Tool no permitida')
            cur = self.db.execute('INSERT INTO tickets(request_id,tenant,summary) VALUES (?,?,?)',
                                  (request_id,principal.tenant,obj['summary']))
            result = {'ticket_id':cur.lastrowid,'request_id':request_id,'state':'DONE'}
            self.db.execute('UPDATE requests SET state=?,result=? WHERE id=?',
                            ('DONE',json.dumps(result),request_id))
            self.db.commit()
            return result
        except Exception:
            self.db.rollback()
            raise

    def ticket_count(self):
        return self.db.execute('SELECT COUNT(*) FROM tickets').fetchone()[0]
