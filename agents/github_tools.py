"""Function calling de GitHub simulado sobre Workflow: cero tráfico y cero issues remotos."""
import json
from .core import Principal, Workflow

CREATE_ISSUE_SCHEMA = {
    'name': 'create_issue',
    'parameters': {'type': 'object', 'properties': {
        'repository': {'type': 'string'}, 'title': {'type': 'string'}, 'body': {'type': 'string'}},
        'required': ['repository', 'title', 'body'], 'additionalProperties': False}}


class GitHubToolDemo:
    def __init__(self, workflow, allowed_repositories):
        self.workflow = workflow
        self.allowed_repositories = frozenset(allowed_repositories)

    def propose(self, request_id, principal, tool_name, arguments):
        if tool_name != CREATE_ISSUE_SCHEMA['name']:
            raise ValueError('Herramienta no permitida')
        if not isinstance(arguments, dict) or set(arguments) != {'repository', 'title', 'body'}:
            raise ValueError('Argumentos inválidos; identidad y permisos se aportan fuera del modelo')
        repository, title, body = (arguments[k] for k in ('repository', 'title', 'body'))
        if not isinstance(repository, str) or repository not in self.allowed_repositories:
            raise PermissionError('Repositorio fuera de la política del servidor')
        if (not isinstance(title, str) or not title.strip() or len(title) > 60
                or not isinstance(body, str) or len(body) > 100):
            raise ValueError('Título o cuerpo inválido')
        # El digest de Workflow incluye repositorio/título/cuerpo, no solo el nombre del tool.
        summary = json.dumps(arguments, sort_keys=True, ensure_ascii=False)
        return self.workflow.propose(request_id, principal, 'create_local_ticket', summary)

    def execute(self, request_id, principal):
        # La aprobación no congela permisos: volver a comprobar la política actual.
        row = self.workflow._row(request_id, principal)
        payload = json.loads(row[2])
        arguments = json.loads(payload['summary'])
        if arguments['repository'] not in self.allowed_repositories:
            raise PermissionError('Repositorio revocado en la política del servidor')
        return dict(self.workflow.execute(request_id, principal), mode='local_simulation', remote_issue_created=False)


def main():
    workflow = Workflow()
    try:
        tool = GitHubToolDemo(workflow, ('focuz-ai/focuz-ai-engineering-agents',))
        author = Principal('A', 'ana', ('support',))
        reviewer = Principal('A', 'bea', ('approver',))
        executor = Principal('A', 'service', ('executor',))
        proposal = tool.propose('github-demo-1', author, 'create_issue', {
            'repository': 'focuz-ai/focuz-ai-engineering-agents',
            'title': 'Añadir regresión de autorización', 'body': 'Fixture educativa; no publicar.'})
        workflow.approve('github-demo-1', reviewer, proposal['digest'])
        print(json.dumps(tool.execute('github-demo-1', executor)))
        print(json.dumps(tool.execute('github-demo-1', executor)))
        print(json.dumps({'local_ticket_count': workflow.ticket_count()}))
    finally:
        workflow.close()


if __name__ == '__main__':
    main()
