# Как поставить скилл

## Агент WAM (Telegram)

Напиши своему агенту:

> Установи скилл https://github.com/nefedkin083/rd-presentations — склонируй
> его в `.claude/skills/rd-presentations` и поставь зависимости из
> `requirements.txt` через `pip install --user`.

Потом:

> Подключи мой Google — Диск и Презентации.

Агент пришлёт ссылку, по ней нужно разрешить доступ. Это делается один раз.

Готово. Дальше просто проси: «сделай презентацию в стиле RD про …» — и
присылай тексты и картинки. Агент сам возьмёт свежую версию скилла перед
каждой презентацией.

## Claude Code и другие агенты со скиллами

```bash
git clone https://github.com/nefedkin083/rd-presentations ~/.claude/skills/rd-presentations
pip install --user -r ~/.claude/skills/rd-presentations/requirements.txt
```

Для сборки в Google нужен либо инструмент Google у агента (путь А в
`SKILL.md`), либо свой OAuth-токен (путь Б).

## Обновления

Скилл обновляется сам: перед каждой работой агент делает `git pull`.
Хочешь поменять что-то в стиле для всех — напиши Ивану или заведи issue
в репозитории. Локальные правки в папке скилла затрутся при обновлении.
