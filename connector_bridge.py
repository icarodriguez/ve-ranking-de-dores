"""Ponte de estado Python para ferramentas Gmail/Drive do Codex, sem tokens de plugins.

python connector_bridge.py {status,prepare,reconcile,confirm} [--input arquivo.json]
"""
import argparse
import json
import logging
from pathlib import Path
from automacao import BASE, Conflict, State, cell_update_requests, lock, make_plan, padded, parse_extraction


def execute(action, payload, state):
    if action == 'status':
        return state.data
    if action in ('prepare', 'review'):
        if action == 'prepare' and state.done(payload['file_id']):
            return {'status': 'already_processed'}
        if state.data['pending']:
            raise Conflict('Resolva pending antes de preparar outra call.')
        extraction = parse_extraction(json.dumps(payload['extraction'], ensure_ascii=False))
        kind = payload.get('source_type', 'comercial')
        if kind not in ('comercial', 'hotseat', 'acompanhamento') or (
                payload.get('eligible_verified') is not True and payload.get('commercial_verified') is not True):
            raise Conflict('Triagem da reunião não confirmada.')
        reviewed = payload.get('reviewed') is True
        if reviewed and not payload.get('evidence'):
            raise Conflict('Revisão exige evidência da fonte.')
        if reviewed and extraction['dor'].casefold() in ('não identificado', 'nao identificado'):
            raise Conflict('Não aprovar dor não identificada.')
        tab = 'Revisar' if extraction['dor_nova'] and not reviewed else 'Dores'
        sid = payload['ids']['B' if extraction['faixa'] == 'B' else 'A']
        if action == 'review':
            previous = state.data['processed'].get(payload['file_id'])
            if not previous:
                raise Conflict('Call ainda não processada.')
            if previous['tab'] == 'Dores':
                return {'status': 'already_reviewed'}
            if not reviewed or tab != 'Dores' or previous['extraction'] != extraction or previous['spreadsheet'] != sid:
                raise Conflict('Revisão deve preservar extração e faixa já confirmadas.')
        if payload.get('tab') != tab or payload.get('spreadsheet') != sid:
            raise Conflict('Destino incompatível com a faixa ou dor_nova.')
        plan = make_plan(payload['file_id'], extraction, sid, tab, payload['rows'])
        plan.update(sheet_id=payload['sheet_id'], source_message_id=payload.get('source_message_id'),
                    source_url=payload.get('source_url'), evidence=payload.get('evidence'),
                    source_type=kind, reviewed=reviewed, operation=action)
        state.prepare(plan)
        return {'status': 'prepared', 'spreadsheet': sid, 'tab': tab, 'row': plan['row']}
    plan = state.data['pending']
    if not plan:
        raise Conflict('Não existe gravação pendente.')
    if any(payload.get(k) != plan[k] for k in ('spreadsheet', 'tab', 'row')):
        raise Conflict('Leitura não corresponde à linha pendente.')
    current, columns = padded(payload['values']), plan['columns']
    if len(columns) == 2 and current[:2] != plan['before'][:2]:
        raise Conflict('Identidade da linha alterada.')
    if all(current[c] == plan['after'][c] for c in columns):
        state.finish()
        logging.info('processada id=%s dor=%s nova=%s destino=%s', plan['file_id'],
                     plan['extraction']['dor'], plan['extraction']['dor_nova'], plan['tab'])
        return {'status': 'confirmed'}
    if action == 'confirm':
        raise Conflict('Gravação não confirmada pelo Google Sheets.')
    if any(current[c] != plan['before'][c] for c in columns):
        raise Conflict('Conteúdo alterado. Não sobrescrever nem recalcular o incremento.')
    requests = cell_update_requests(plan, plan['sheet_id'])
    return {'status': 'write_required', 'spreadsheet_id': plan['spreadsheet'], 'requests': requests}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['status', 'prepare', 'review', 'reconcile', 'confirm'])
    parser.add_argument('--input', type=Path)
    args = parser.parse_args()
    payload = json.loads(args.input.read_text()) if args.input else {}
    logging.basicConfig(filename=BASE / 'automacao.log', level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    path = BASE / 'processados.json'
    with lock(str(path) + '.lock'):
        result = execute(args.action, payload, State(path))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
