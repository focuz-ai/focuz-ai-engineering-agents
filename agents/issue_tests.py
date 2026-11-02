"""Leer issue ficticia -> cinco casos de QA por plantilla -> Markdown para revisión."""
import argparse
from dataclasses import dataclass
import html
import json
from pathlib import Path
from .core import Principal


@dataclass(frozen=True)
class Issue:
    tenant: str
    repository: str
    issue_id: int
    title: str
    spec_kind: str


def read_issue(issues, principal, repository, issue_id, allowed_repositories):
    if 'qa' not in principal.roles or repository not in allowed_repositories:
        raise PermissionError('Lectura de issue no autorizada')
    for issue in issues:
        if (issue.tenant == principal.tenant and issue.repository == repository
                and type(issue_id) is int and issue.issue_id == issue_id):
            return issue
    raise PermissionError('Issue no accesible')


def propose_cases(issue, principal):
    if 'qa' not in principal.roles or issue.tenant != principal.tenant:
        raise PermissionError('No puede preparar casos para esta issue')
    if issue.spec_kind != 'approved_ticket':
        raise ValueError('Plantilla no disponible para este requisito; requiere análisis humano')
    # El contenido de la issue es datos: no se ejecutan instrucciones ni código.
    scenarios = [
        ('happy', 'Propuesta de A aprobada por otra persona; executor vigente',
         'Ejecutar una vez', 'DONE; exactamente un ticket de A'),
        ('missing_approval', 'Propuesta de A en PENDING',
         'Ejecutar sin aprobación', 'PermissionError; cero tickets'),
        ('foreign_tenant', 'Propuesta aprobada de A; executor de B',
         'Ejecutar desde B', 'PermissionError; cero tickets'),
        ('replay', 'Propuesta aprobada de A; executor vigente',
         'Ejecutar dos veces con el mismo ID', 'Mismo ticket_id; contador total 1'),
        ('revoked_role', 'Solicitud DONE de A; usuario pierde rol executor',
         'Reintentar después de revocar el rol', 'PermissionError; contador se mantiene en 1')]
    return [{'issue_id': issue.issue_id, 'test_id': f'ISSUE-{issue.issue_id}-{index:02d}',
             'scenario': scenario, 'preconditions': preconditions, 'input': action, 'expected': expected}
            for index, (scenario, preconditions, action, expected) in enumerate(scenarios, 1)]


def markdown_text(value):
    value = html.escape(str(value)).replace('\r', ' ').replace('\n', ' ')
    for character in ('\\', '|', '[', ']', '`', '*', '_'):
        value = value.replace(character, '\\' + character)
    return value


def render_markdown(issue, cases):
    lines = [f'# Casos propuestos para issue {issue.issue_id}: {markdown_text(issue.title)}', '',
             'Plantilla determinista Focuz. Datos ficticios; sin LLM ni acceso a GitHub.',
             'Estado: propuestos para revisión. Este archivo no demuestra que las pruebas se hayan ejecutado.', '',
             '| issue_id | test_id | Precondiciones | Entrada | Resultado esperado |',
             '| --- | --- | --- | --- | --- |']
    for case in cases:
        if case['issue_id'] != issue.issue_id:
            raise ValueError('Caso asociado a otra issue')
        lines.append('| ' + ' | '.join(markdown_text(case[key]) for key in
                     ('issue_id', 'test_id', 'preconditions', 'input', 'expected')) + ' |')
    lines.extend(['', 'Antes de llevarlo al IDE: revisar requisitos, adaptar fixtures y convertir cada caso en una prueba.',
                  'Conservar el comando, resultado y commit evaluado. Una propuesta de caso no cuenta como prueba aprobada.', ''])
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export', type=Path, help='Crear Markdown nuevo; no sobrescribe')
    args = parser.parse_args()
    repository = 'focuz-ai/focuz-ai-engineering-agents'
    issue = read_issue([Issue('A', repository, 17, 'Ticket con aprobación humana e idempotencia', 'approved_ticket')],
                      Principal('A', 'qa-user', ('qa',)), repository, 17, (repository,))
    cases = propose_cases(issue, Principal('A', 'qa-user', ('qa',)))
    if args.export:
        with args.export.open('x', encoding='utf-8') as stream:
            stream.write(render_markdown(issue, cases))
    print(json.dumps({'issue_id': issue.issue_id, 'cases': cases, 'case_count': len(cases),
                      'status': 'proposed_not_executed', 'mode': 'deterministic_template',
                      'exported': str(args.export) if args.export else None}, ensure_ascii=False))


if __name__ == '__main__':
    main()
