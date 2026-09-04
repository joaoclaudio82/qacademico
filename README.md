# Preencher diários do Q-Acadêmico

O script entra como professor, **coleta as disciplinas do período**, pede à IA uma **ementa por tópicos** e usa esses tópicos para lançar conteúdo e frequência das aulas já ocorridas.

## Fluxo

1. Login no Q-Acadêmico
2. Coleta de cada diário: nome, curso, carga horária, calendário e o que já foi lançado
3. A IA gera, para cada disciplina:
   - ementa
   - objetivos
   - um tópico por encontro do semestre
4. A proposta é salva em `ementas.json` e impressa no terminal
5. As aulas passadas sem conteúdo recebem o tópico correspondente àquela data
6. A frequência é salva com todos presentes

Aulas futuras entram na ementa, mas **não são lançadas** até a data ocorrer.

## Requisitos

- Python 3.10 ou superior
- Uma chave de IA (Gemini é gratuita): [Google AI Studio](https://aistudio.google.com/apikey)

## Instalação

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
```

Crie um `.env` a partir do `.env.example`:

```
QACADEMICO_LOGIN=seu_login
QACADEMICO_SENHA=sua_senha
GEMINI_API_KEY=sua_chave_gemini
```

## Uso

Só login e senha (a chave de IA pode estar no `.env`):

```powershell
python preencher_diarios.py
```

Só gerar e revisar a ementa, sem lançar no Q-Acadêmico:

```powershell
python preencher_diarios.py --somente-ementas
```

Gerar de novo, ignorando `ementas.json`:

```powershell
python preencher_diarios.py --regenerar --somente-ementas
```

Simular o lançamento:

```powershell
python preencher_diarios.py --dry-run
```

| Opção | Efeito |
| --- | --- |
| `--somente-ementas` | Coleta disciplinas e gera a proposta, sem salvar aulas |
| `--regenerar` | Pede ementa nova à IA |
| `--dry-run` | Mostra o que seria lançado, sem gravar no sistema |
| `--headless` | Roda sem abrir a janela do navegador |

Na segunda execução, se `ementas.json` já existir, a ementa é reaproveitada (a menos que use `--regenerar`). Você pode editar os tópicos nesse arquivo antes de lançar.

## Observações

- Revise a ementa em `ementas.json` antes de lançar de verdade.
- Não compartilhe senha nem chave de IA no Git.
- Use só na sua conta de professor.
