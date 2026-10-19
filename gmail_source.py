"""Fonte Gmail somente leitura; resolve anexos e Docs vinculados, incluindo abas."""
import base64
from html import unescape
from html.parser import HTMLParser
import re
from pathlib import Path
from automacao import InvalidExtraction, LOG, normalize, retry

DEFAULT_QUERY = ('-in:spam -in:trash '
    '{from:gemini-notes@google.com from:meetings-noreply@google.com from:fireflies.ai from:tldv.io from:supernormal.com} '
    '{subject:diagnóstico subject:diagnostico subject:Vinicius subject:Vinícius subject:Matheus subject:Carla subject:Hotseat subject:"Hot seat" subject:VE subject:PEV subject:PVE subject:VFP}')


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.links += [v for k, v in attrs if k == 'href' and v]


def parts(payload):
    yield payload
    for part in payload.get('parts', []) or []:
        yield from parts(part)


def decode(data):
    return base64.urlsafe_b64decode(data + '=' * (-len(data) % 4)).decode('utf-8-sig')


def body_text(part):
    body = part.get('body', {})
    return body.get('content') or decode(body['data']) if (body.get('content') or body.get('data')) else ''


def doc_ids(payload):
    ids = []
    for part in parts(payload):
        text = body_text(part)
        for match in re.finditer(r'https://(?:docs\.google\.com/document/d/|drive\.google\.com/file/d/)([\w-]+)', unescape(text)):
            if match[1] not in ids:
                ids.append(match[1])
    return ids


def document_text(document):
    def text(node):
        if not isinstance(node, dict):
            return ''
        if 'textRun' in node:
            return node['textRun'].get('content', '')
        return ''.join(text(c) for v in node.values() for c in (v if isinstance(v, list) else [v]) if isinstance(c, dict))
    def tabs(nodes):
        for tab in nodes:
            yield tab
            yield from tabs(tab.get('childTabs', []))
    selected = []
    for tab in tabs(document.get('tabs', [])):
        title = tab.get('tabProperties', {}).get('title', tab.get('title', ''))
        if 'transcri' in normalize(title) and 'chat' not in normalize(title):
            selected.append(text(tab.get('documentTab', tab).get('body', {})))
    if selected:
        return '\n'.join(selected)
    # Arquivo avulso explicitamente identificado como transcrição.
    if 'transcri' in normalize(document.get('title', '')) and 'chat' not in normalize(document.get('title', '')):
        return text(document.get('body', {})) or '\n'.join(text(t.get('documentTab', t).get('body', {})) for t in tabs(document.get('tabs', [])))
    raise InvalidExtraction('Documento contém notas, mas não transcrição completa.')


class GmailSource:
    def __init__(self, gmail, drive, docs, account, query, max_chars):
        self.gmail, self.drive, self.docs = gmail.users(), drive.files(), docs.documents()
        self.account, self.query, self.max_chars = account, query, max_chars

    def transcripts(self):
        profile = retry(lambda: self.gmail.getProfile(userId='me').execute())
        if profile['emailAddress'].lower() != self.account.lower():
            raise InvalidExtraction('Gmail conectado à conta incorreta.')
        page, messages = None, []
        while True:
            result = retry(lambda: self.gmail.messages().list(userId='me', q=self.query,
                            maxResults=100, pageToken=page).execute())
            for message in result.get('messages', []):
                full = retry(lambda: self.gmail.messages().get(userId='me', id=message['id'], format='full').execute())
                messages.append(full)
            page = result.get('nextPageToken')
            if not page:
                break
        # Gmail retorna recentes primeiro; ordenar TODAS as páginas antes de processar.
        for message in sorted(messages, key=lambda m: (int(m['internalDate']), m['id'])):
            subject = next((h['value'] for h in message['payload'].get('headers', []) if h['name'].lower() == 'subject'), '')
            if any(x in normalize(subject) for x in ('onboarding', 'mkt & vendas')):
                LOG.info('ignorada_reuniao_interna id=%s', message['id'])
                continue
            yield {'id': 'gmail:' + message['id'], 'name': subject, 'message': message}

    def text(self, file):
        message = file['message']
        for did in doc_ids(message['payload']):
            meta = retry(lambda: self.drive.get(fileId=did, fields='id,name,mimeType', supportsAllDrives=True).execute())
            if re.search(r'\bchat\b', normalize(meta['name'])):
                continue
            if meta['mimeType'] == 'application/vnd.google-apps.document':
                doc = retry(lambda: self.docs.get(documentId=did, includeTabsContent=True).execute())
                try:
                    text = document_text(doc)
                except InvalidExtraction:
                    continue
            elif Path(meta['name']).suffix.lower() in ('.txt', '.srt', '.vtt'):
                text = retry(lambda: self.drive.get_media(fileId=did, supportsAllDrives=True).execute()).decode('utf-8-sig')
            else:
                continue
            file['id'] = did  # Mesmo ID canônico no modo API e no modo conector.
            return self.validate(text)
        for part in parts(message['payload']):
            if Path(part.get('filename', '')).suffix.lower() not in ('.txt', '.srt', '.vtt'):
                continue
            body = part.get('body', {})
            if body.get('attachmentId'):
                aid = body['attachmentId']
                raw = retry(lambda: self.gmail.messages().attachments().get(userId='me', messageId=message['id'], id=aid).execute())
                text = decode(raw['data'])
            else:
                text = body_text(part)
            return self.validate(text)
        # Não confundir itens de ação, resumo ou aviso com transcrição.
        for part in parts(message['payload']):
            if part.get('mimeType', part.get('mime_type')) == 'text/plain':
                text = body_text(part)
                marker = re.search(r'(?:^|\n)(?:TRANSCRIÇÃO COMPLETA|FULL TRANSCRIPT)\s*:?\s*\n', text, re.I)
                if marker:
                    return self.validate(text[marker.end():])
        raise InvalidExtraction('Sem transcrição completa acessível; vídeo/chat/link externo fica pendente.')

    def validate(self, text):
        if not text.strip() or len(text) > self.max_chars:
            raise InvalidExtraction('Transcrição vazia ou acima do limite.')
        return text
