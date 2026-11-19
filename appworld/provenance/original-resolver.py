"""Frozen pilot resolver: instructions and initial contacts are its only inputs."""
import json
import re

ROLES='partner|husband|wife|manager|parents|parent|coworkers|coworker|roommates|roommate|siblings|sibling'

PATTERNS=[
 ('explicit_recipient', re.compile(r'\b(?:send|forward|email)\b[^.!?]*?\bto\s+(?:both\s+)?my\s+('+ROLES+r')\b',re.I)),
 ('reply_to_requester',re.compile(r'^My\s+(manager|husband|wife|partner)\b(?=[\s\S]*\b(?:reply|response)\b)',re.I)),
 ('shared_trip',re.compile(r'\btrip\b[^.!?]*\bwith my\s+(roommates|siblings)\b(?=[\s\S]*\b(?:email|e-mail)\b)',re.I)),
 ('coworker_group',re.compile(r'\beach of my\s+(coworkers)\b(?=[\s\S]*\bemail\b[\s\S]*\ball of them\b)',re.I)),
]

def resolve(instruction,contacts):
    hits=[(label,m.group(1).lower().removesuffix('s')) for label,pat in PATTERNS for m in pat.finditer(instruction)]
    roles={r for _,r in hits}
    if len(roles)!=1:return {'status':'abstain','reason':'no_unique_supported_role','matches':hits}
    role=next(iter(roles))
    selected=[]
    for c in contacts:
        relationships=c['relationships']
        if isinstance(relationships,str):relationships=json.loads(relationships)
        if role in {r.casefold() for r in relationships}:selected.append(c['email'].casefold())
    selected=sorted(set(selected))
    if not selected:return {'status':'abstain','reason':'role_not_in_contacts','role':role,'matches':hits}
    if role in {'manager','partner','husband','wife'} and len(selected)!=1:
        return {'status':'abstain','reason':'nonunique_single_person_role','role':role,'matches':hits}
    return {'status':'resolved','role':role,'allowed':selected,'matches':hits}
