import json
from .core import Workflow, Principal

workflow = Workflow()
try:
    proposal = workflow.propose('demo-001',Principal('A','ana',('support',)),
                                'create_local_ticket','Revisar política ficticia de soporte')
    print(json.dumps(proposal,ensure_ascii=False))
    workflow.approve('demo-001',Principal('A','luisa',('approver',)),proposal['digest'])
    executor = Principal('A','service',('executor',))
    print(json.dumps(workflow.execute('demo-001',executor)))
    print(json.dumps({'retry_result':workflow.execute('demo-001',executor),
                      'tickets':workflow.ticket_count()}))
finally:
    workflow.close()
