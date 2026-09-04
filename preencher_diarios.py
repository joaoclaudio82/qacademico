#!/usr/bin/env python3
"""
Coleta as disciplinas do professor no Q-Acadêmico, pede à IA uma ementa
por tópicos e usa esses tópicos para lançar conteúdo e frequência.

Uso:
    python preencher_diarios.py --login SEU_LOGIN --senha SUA_SENHA
    python preencher_diarios.py --somente-ementas
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

from ia_ementa import gerar_proposta, provedor_ia

BASE = "https://antigo.qacademico.ifce.edu.br"
LOGIN_URL = f"{BASE}/qacademico/index.asp?t=1000"
FREQUENCIA_URL = f"{BASE}/webapp/lancamento-frequencia"
DASHBOARD_URL = f"{BASE}/webapp/dashboard"
EMENTAS_PATH = Path(__file__).resolve().parent / "ementas.json"


def parse_data_br(texto: str) -> date | None:
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", texto or "")
    if not m:
        return None
    return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))


def parse_data_iso(texto: str | None) -> date | None:
    if not texto:
        return None
    try:
        return date.fromisoformat(texto[:10])
    except ValueError:
        return None


def hora_do_titulo(titulo: str) -> str:
    m = re.search(r"(\d{1,2}:\d{2})", titulo or "")
    return m.group(1) if m else ""


def credenciais(args: argparse.Namespace) -> tuple[str, str]:
    load_dotenv(Path(__file__).resolve().parent / ".env")
    login = args.login or os.getenv("QACADEMICO_LOGIN") or input("Login do professor: ").strip()
    senha = args.senha or os.getenv("QACADEMICO_SENHA") or getpass.getpass("Senha: ")
    if not login or not senha:
        sys.exit("Informe login e senha.")
    return login, senha


def esperar_angular(page, seletor: str, timeout: int = 30000) -> None:
    page.wait_for_selector(seletor, timeout=timeout)
    page.wait_for_timeout(800)


def entrar(page, login: str, senha: str) -> None:
    page.goto(LOGIN_URL, wait_until="domcontentloaded")
    if page.locator("a", has_text="PROFESSOR").count():
        page.locator("a", has_text="PROFESSOR").first.click()
        page.wait_for_load_state("domcontentloaded")

    page.wait_for_selector("#txtLogin", timeout=20000)
    page.fill("#txtLogin", login)
    page.fill("#txtSenha", senha)
    page.click("#btnOk")

    try:
        page.wait_for_function(
            """() => {
                const t = document.body ? document.body.innerText : '';
                return /Meus Diários|Boa (noite|tarde|dia)|Minhas Disciplinas|Lançamento de Frequência/i.test(t)
                    && !document.querySelector('#txtLogin');
            }""",
            timeout=25000,
        )
    except PlaywrightTimeout:
        texto = page.locator("body").inner_text()
        if re.search(r"inválid|incorret|senha|não cadastrad", texto, re.I):
            sys.exit("Falha no login: usuário ou senha rejeitados pelo Q-Acadêmico.")
        sys.exit("Falha no login: a página do professor não abriu. Confira login, senha e a rede.")


def listar_diarios(page) -> list[dict]:
    page.goto(DASHBOARD_URL, wait_until="domcontentloaded")
    esperar_angular(page, "text=Minhas Disciplinas em Curso")
    page.wait_for_timeout(1500)

    if page.get_by_text("Diários por período", exact=False).count():
        page.get_by_text("Diários por período", exact=False).first.click()
        page.wait_for_timeout(1200)

    diarios = page.evaluate(
        """() => {
            const seen = new Map();
            for (const a of document.querySelectorAll('a[href*="idDiario="]')) {
                const m = a.href.match(/idDiario=(\\d+)/);
                if (!m) continue;
                const id = m[1];
                const texto = (a.innerText || a.textContent || '').replace(/\\s+/g, ' ').trim();
                if (!seen.has(id)) seen.set(id, {id, nome: texto || id});
            }
            return Array.from(seen.values());
        }"""
    )
    if diarios:
        return diarios

    page.goto(FREQUENCIA_URL, wait_until="domcontentloaded")
    esperar_angular(page, "text=Lançamento de Frequência")
    return page.evaluate(
        """() => {
            const sel = document.querySelector('select');
            if (!sel) return [];
            return Array.from(sel.options).map(o => ({
                id: (o.textContent.match(/^\\s*(\\d+)/) || [])[1] || '',
                nome: (o.textContent || '').replace(/\\s+/g, ' ').trim()
            })).filter(d => d.id);
        }"""
    )


def titulos_mes(page) -> list[dict]:
    return page.evaluate(
        """() => Array.from(document.querySelectorAll('.fc-day-grid-event')).map((a, i) => {
            const row = a.closest('.fc-row');
            const td = a.closest('td');
            const idx = td && td.parentElement ? Array.from(td.parentElement.children).indexOf(td) : -1;
            const day = row && idx >= 0 ? row.querySelectorAll('.fc-day')[idx] : null;
            return {
                i,
                titulo: (a.innerText || '').replace(/\\s+/g, ' ').trim(),
                data: day ? day.getAttribute('data-date') : null
            };
        })"""
    )


def ir_mes(page, direcao: str) -> str:
    botao = ".fc-prev-button" if direcao == "prev" else ".fc-next-button"
    try:
        page.wait_for_selector(botao, timeout=12000)
    except PlaywrightTimeout:
        return ""
    page.locator(botao).first.click()
    page.wait_for_timeout(700)
    return page.locator("h2").first.inner_text() if page.locator("h2").count() else ""


def mes_do_cabecalho(page) -> date | None:
    if not page.locator("h2").count():
        return None
    cab = page.locator("h2").first.inner_text()
    m = re.search(
        r"(janeiro|fevereiro|março|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)\s+(\d{4})",
        cab,
        re.I,
    )
    if not m:
        return None
    meses = {
        "janeiro": 1, "fevereiro": 2, "março": 3, "abril": 4, "maio": 5, "junho": 6,
        "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
    }
    return date(int(m.group(2)), meses[m.group(1).lower()], 1)


def ir_para_mes(page, alvo: date) -> bool:
    for _ in range(16):
        atual = mes_do_cabecalho(page)
        if not atual:
            return False
        if (atual.year, atual.month) == (alvo.year, alvo.month):
            return True
        ir_mes(page, "prev" if (atual.year, atual.month) > (alvo.year, alvo.month) else "next")
    return False


def abrir_diario(page, diario_id: str) -> bool:
    page.goto(f"{FREQUENCIA_URL}?idDiario={diario_id}", wait_until="domcontentloaded")
    esperar_angular(page, "text=Lançamento de Frequência")
    try:
        page.wait_for_selector(".fc-prev-button, .fc-day-grid-event", timeout=25000)
    except PlaywrightTimeout:
        return False
    page.wait_for_timeout(800)
    return True


def metadados_diario(page, bruto: dict) -> dict:
    texto = page.locator("body").inner_text()
    nome_bruto = bruto.get("nome") or ""
    m_nome = re.search(
        r"\d+\s*-\s*\S+\s*-\s*(.+)$",
        nome_bruto,
    )
    nome_disc = (m_nome.group(1) if m_nome else nome_bruto).strip()

    curso = ""
    for linha in texto.splitlines():
        linha = linha.strip()
        if re.search(r"Bacharelado|Licenciatura|Tecnologia|Mestrado|Técnico|Especialização", linha, re.I):
            curso = linha
            break

    m_ch = re.search(r"(\d+)\s*horas aulas necessárias", texto, re.I)
    m_vig = re.search(r"Vigência do Diário\s+(\d{2}/\d{2}/\d{4})\s+a\s+(\d{2}/\d{2}/\d{4})", texto, re.I)
    inicio = parse_data_br(m_vig.group(1)) if m_vig else None
    fim = parse_data_br(m_vig.group(2)) if m_vig else None

    return {
        "id": bruto["id"],
        "nome": nome_bruto,
        "nome_disciplina": nome_disc,
        "curso": curso,
        "carga_horaria": f"{m_ch.group(1)}h" if m_ch else "",
        "vigencia_inicio": inicio.isoformat() if inicio else None,
        "vigencia_fim": fim.isoformat() if fim else None,
    }


def varrer_aulas(page, inicio: date | None, fim: date | None) -> list[dict]:
    hoje = date.today()
    alvo = inicio or date(hoje.year, max(1, hoje.month - 2), 1)
    ir_para_mes(page, date(alvo.year, alvo.month, 1))

    limite = fim or date(hoje.year, 12, 31)
    vistos: set[str] = set()
    aulas: list[dict] = []

    for _ in range(12):
        mes = mes_do_cabecalho(page)
        for ev in titulos_mes(page):
            data = ev.get("data")
            if not data:
                continue
            hora = hora_do_titulo(ev.get("titulo") or "")
            chave = f"{data}|{hora}|{ev.get('titulo')}"
            if chave in vistos:
                continue
            vistos.add(chave)
            titulo = ev.get("titulo") or ""
            preenchida = "sem conteúdo" not in titulo.lower()
            conteudo = ""
            if preenchida:
                conteudo = re.sub(r"^\d{1,2}:\d{2}\s*", "", titulo).strip()
            aulas.append({
                "data": data,
                "hora": hora,
                "titulo": titulo,
                "preenchida": preenchida,
                "conteudo": conteudo,
            })
        if mes and (mes.year, mes.month) >= (limite.year, limite.month):
            break
        ir_mes(page, "next")

    aulas.sort(key=lambda a: (a["data"] or "", a["hora"] or ""))
    for i, aula in enumerate(aulas):
        aula["indice"] = i
    return aulas


def coletar_disciplina(page, bruto: dict) -> dict | None:
    if not abrir_diario(page, bruto["id"]):
        print(f"    Calendário não carregou: {bruto.get('nome')}")
        return None
    info = metadados_diario(page, bruto)
    inicio = parse_data_iso(info.get("vigencia_inicio"))
    fim = parse_data_iso(info.get("vigencia_fim"))
    aulas = varrer_aulas(page, inicio, fim)
    info["aulas"] = aulas
    info["encontros"] = len(aulas)
    info["conteudos_lancados"] = [
        f"{a['data']} {a['hora']}: {a['conteudo']}"
        for a in aulas if a.get("preenchida") and a.get("conteudo")
    ]
    return info


def imprimir_proposta(disc: dict) -> None:
    print(f"\n{'=' * 72}")
    print(f"{disc.get('nome_disciplina') or disc.get('nome')}")
    if disc.get("curso"):
        print(f"Curso: {disc['curso']}")
    extra = []
    if disc.get("carga_horaria"):
        extra.append(disc["carga_horaria"])
    extra.append(f"{disc.get('encontros', 0)} encontros")
    print(" | ".join(extra))
    print("-" * 72)
    if disc.get("ementa"):
        print("Ementa:")
        print(f"  {disc['ementa']}")
    if disc.get("objetivos"):
        print("Objetivos:")
        for obj in disc["objetivos"]:
            print(f"  - {obj}")
    print("Tópicos das aulas:")
    for i, topico in enumerate(disc.get("topicos") or [], start=1):
        aula = disc["aulas"][i - 1] if i - 1 < len(disc.get("aulas") or []) else {}
        marca = "já lançada" if aula.get("preenchida") else "a lançar"
        quando = f"{aula.get('data', '')} {aula.get('hora', '')}".strip()
        print(f"  {i:02d}. [{marca}] {quando} — {topico}")


def salvar_ementas(disciplinas: list[dict], caminho: Path) -> None:
    payload = {
        "gerado_em": date.today().isoformat(),
        "provedor": provedor_ia(),
        "disciplinas": [
            {
                "id": d["id"],
                "nome": d.get("nome"),
                "nome_disciplina": d.get("nome_disciplina"),
                "curso": d.get("curso"),
                "carga_horaria": d.get("carga_horaria"),
                "encontros": d.get("encontros"),
                "ementa": d.get("ementa"),
                "objetivos": d.get("objetivos"),
                "topicos": d.get("topicos"),
                "aulas": d.get("aulas"),
            }
            for d in disciplinas
        ],
    }
    caminho.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nProposta salva em {caminho}")


def carregar_ementas(caminho: Path) -> dict[str, dict]:
    if not caminho.exists():
        return {}
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    return {d["id"]: d for d in dados.get("disciplinas") or [] if d.get("id")}


def fechar_modal(page) -> None:
    if page.get_by_role("button", name=re.compile(r"^Cancelar$")).count():
        try:
            page.evaluate(
                """() => {
                    const btn = Array.from(document.querySelectorAll('button'))
                        .find(b => b.innerText.trim() === 'Cancelar');
                    if (btn && window.angular) angular.element(btn).triggerHandler('click');
                    else if (btn) btn.click();
                }"""
            )
            page.wait_for_timeout(400)
        except Exception:
            pass


def preencher_textarea(page, texto: str) -> None:
    page.evaluate(
        """(conteudo) => {
            const ta = document.querySelector('textarea');
            if (!ta) return;
            const setter = Object.getOwnPropertyDescriptor(
                window.HTMLTextAreaElement.prototype, 'value'
            ).set;
            setter.call(ta, conteudo);
            ta.dispatchEvent(new Event('input', {bubbles: true}));
            ta.dispatchEvent(new Event('change', {bubbles: true}));
            if (window.angular) {
                angular.element(ta).triggerHandler('input');
                angular.element(ta).triggerHandler('change');
            }
        }""",
        texto,
    )
    page.wait_for_timeout(200)


def clicar_angular(page, texto_botao: str, ng_click_contem: str | None = None) -> bool:
    return page.evaluate(
        """({texto, ng}) => {
            const btns = Array.from(document.querySelectorAll('button'))
                .filter(b => b.innerText.replace(/\\s+/g, ' ').includes(texto));
            let btn = ng
                ? btns.find(b => (b.getAttribute('ng-click') || '').includes(ng))
                : null;
            btn = btn || btns[0];
            if (!btn || btn.disabled) return false;
            if (window.angular) angular.element(btn).triggerHandler('click');
            else btn.click();
            return true;
        }""",
        {"texto": texto_botao, "ng": ng_click_contem},
    )


def clicar_evento(page, data_iso: str, hora: str) -> bool:
    return page.evaluate(
        """({data, hora}) => {
            const evs = Array.from(document.querySelectorAll('.fc-day-grid-event'));
            for (const a of evs) {
                const row = a.closest('.fc-row');
                const td = a.closest('td');
                const idx = td && td.parentElement ? Array.from(td.parentElement.children).indexOf(td) : -1;
                const day = row && idx >= 0 ? row.querySelectorAll('.fc-day')[idx] : null;
                const d = day ? day.getAttribute('data-date') : null;
                const t = (a.innerText || '').replace(/\\s+/g, ' ');
                if (d === data && (!hora || t.includes(hora))) {
                    a.click();
                    return true;
                }
            }
            return false;
        }""",
        {"data": data_iso, "hora": hora},
    )


def lancar_aula(page, conteudo: str, dry_run: bool) -> bool:
    page.wait_for_timeout(500)
    corpo = page.locator("body").inner_text()
    if re.search(r"ainda não ocorreu", corpo, re.I):
        fechar_modal(page)
        return False

    cabecalho = page.locator("h6").first.inner_text() if page.locator("h6").count() else ""
    data_aula = parse_data_br(cabecalho) or parse_data_br(corpo)
    if data_aula and data_aula > date.today():
        fechar_modal(page)
        return False

    ta = page.locator("textarea").first
    if not ta.count():
        fechar_modal(page)
        return False

    atual = (ta.input_value() or "").strip()
    if atual and "sem conteúdo" not in atual.lower():
        fechar_modal(page)
        return False

    if dry_run:
        print(f"    [simulação] {cabecalho or 'aula'}: {conteudo}")
        fechar_modal(page)
        return True

    preencher_textarea(page, conteudo)
    page.wait_for_timeout(300)
    if not clicar_angular(page, "Salvar e Lançar Frequência", "salvarConteudoAula"):
        fechar_modal(page)
        return False

    try:
        page.wait_for_function(
            "() => /As faltas devem ser salvas/i.test(document.body.innerText)",
            timeout=15000,
        )
    except PlaywrightTimeout:
        fechar_modal(page)
        return False

    if not clicar_angular(page, "Salvar", "salvarFaltas"):
        fechar_modal(page)
        return False

    page.wait_for_timeout(900)
    fechar_modal(page)
    return True


def lancar_disciplina(page, disc: dict, dry_run: bool, hoje: date) -> int:
    if not abrir_diario(page, disc["id"]):
        print("    Calendário não carregou.")
        return 0

    topicos = disc.get("topicos") or []
    preenchidas = 0
    print(f"\n== Lançando {disc.get('nome_disciplina') or disc.get('nome')} ==")

    for aula in disc.get("aulas") or []:
        data_aula = parse_data_iso(aula.get("data"))
        if not data_aula or data_aula > hoje or aula.get("preenchida"):
            continue
        indice = int(aula.get("indice") or 0)
        conteudo = topicos[indice] if indice < len(topicos) else None
        if not conteudo:
            continue
        if not ir_para_mes(page, data_aula):
            print(f"    -- {aula.get('data')}: mês não encontrado")
            continue
        if not clicar_evento(page, aula["data"], aula.get("hora") or ""):
            print(f"    -- {aula.get('data')} {aula.get('hora')}: evento não encontrado")
            continue
        if lancar_aula(page, conteudo, dry_run):
            preenchidas += 1
            aula["preenchida"] = True
            print(f"    OK {aula.get('data')} {aula.get('hora')}: {conteudo}")
        else:
            print(f"    -- {aula.get('data')} {aula.get('hora')}: pulada")

    print(f"    Lançadas neste diário: {preenchidas}")
    return preenchidas


def gerar_ementas_ia(disciplinas: list[dict], cache: dict[str, dict], regenerar: bool) -> None:
    if not provedor_ia():
        sys.exit(
            "Configure uma chave de IA no .env: GEMINI_API_KEY (recomendada e gratuita), "
            "OPENAI_API_KEY ou GROQ_API_KEY."
        )

    for disc in disciplinas:
        antigo = cache.get(disc["id"]) if not regenerar else None
        if antigo and antigo.get("topicos") and len(antigo["topicos"]) >= disc["encontros"]:
            disc["ementa"] = antigo.get("ementa")
            disc["objetivos"] = antigo.get("objetivos") or []
            disc["topicos"] = antigo["topicos"][: disc["encontros"]]
            print(f"  Ementa reaproveitada: {disc['nome_disciplina']}")
            continue
        print(f"  Gerando ementa com IA: {disc['nome_disciplina']} ({disc['encontros']} tópicos)...")
        proposta = gerar_proposta(disc)
        disc["ementa"] = proposta.get("ementa")
        disc["objetivos"] = proposta.get("objetivos") or []
        disc["topicos"] = proposta.get("topicos") or []


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Coleta disciplinas, gera ementa com IA e lança as aulas no Q-Acadêmico."
    )
    parser.add_argument("--login", help="Login do professor")
    parser.add_argument("--senha", help="Senha do professor")
    parser.add_argument("--headless", action="store_true", help="Executa sem abrir a janela do navegador")
    parser.add_argument("--dry-run", action="store_true", help="Gera a ementa, mas não salva no Q-Acadêmico")
    parser.add_argument("--somente-ementas", action="store_true", help="Só coleta disciplinas e gera a proposta")
    parser.add_argument("--regenerar", action="store_true", help="Ignora ementas.json e gera de novo com a IA")
    args = parser.parse_args()

    login, senha = credenciais(args)
    hoje = date.today()
    cache = carregar_ementas(EMENTAS_PATH)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless, slow_mo=80)
        context = browser.new_context(locale="pt-BR", viewport={"width": 1400, "height": 900})
        page = context.new_page()
        page.set_default_timeout(30000)

        print("1/3 Entrando no Q-Acadêmico...")
        entrar(page, login, senha)

        print("2/3 Coletando disciplinas do professor...")
        brutos = listar_diarios(page)
        if not brutos:
            browser.close()
            sys.exit("Nenhum diário encontrado para este professor no período atual.")

        disciplinas: list[dict] = []
        for bruto in brutos:
            print(f"  Lendo {bruto['nome']}...")
            info = coletar_disciplina(page, bruto)
            if info:
                disciplinas.append(info)
                print(f"    {info['encontros']} encontros | já lançados: {len(info['conteudos_lancados'])}")

        if not disciplinas:
            browser.close()
            sys.exit("Não foi possível ler o calendário das disciplinas.")

        print("3/3 Pedindo à IA uma ementa por tópicos...")
        gerar_ementas_ia(disciplinas, cache, args.regenerar)
        salvar_ementas(disciplinas, EMENTAS_PATH)
        for disc in disciplinas:
            imprimir_proposta(disc)

        if args.somente_ementas:
            browser.close()
            print("\nParado em --somente-ementas. Revise ementas.json e rode de novo para lançar.")
            return

        total = 0
        for disc in disciplinas:
            try:
                total += lancar_disciplina(page, disc, args.dry_run, hoje)
            except PlaywrightTimeout as exc:
                print(f"    Erro de tempo em {disc.get('nome')}: {exc}")
            except Exception as exc:
                print(f"    Erro em {disc.get('nome')}: {exc}")

        browser.close()

    modo = "simuladas" if args.dry_run else "lançadas"
    print(f"\nConcluído. Aulas {modo}: {total}")
    print("Aulas futuras ficam só na proposta da ementa até a data ocorrer.")


if __name__ == "__main__":
    main()
