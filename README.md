# RD Presentations

Скилл для агентов: собирает презентации в Google Презентациях в фирменном
стиле Radugadesign и правит их по правилам студии. Один на всю студию —
правила обновляются у всех, кто его поставил.

- [INSTALL.md](INSTALL.md) — как поставить себе.
- [SKILL.md](SKILL.md) — порядок работы агента.
- [RULES.md](RULES.md) — правила вёрстки RD.
- [MANIFEST.md](MANIFEST.md) — архетипы слайдов и поля.
- [examples/example.yaml](examples/example.yaml) — пример манифеста.

## Как устроено

```
deck.yaml ──rdp build──▶ plan/ (requests.json + media/ — картинки уже скруглены)
                              │
          путь А: агент грузит media через свою интеграцию Google → rdp finalize → batchUpdate
          путь Б: rdp direct --token token.json
                              ▼
                     Google Презентация
```

Сборка плана не ходит в Google, поэтому её можно проверить без ключей —
это и делает CI.

## Как меняется стиль

Правило вёрстки — в `RULES.md`, число — в `rdp/dna.yaml`, поведение — в
`rdp/build.py`. Изменения только через pull request: issue → ветка → PR →
зелёный CI → merge. Правило, которое есть в тексте, но не в сборщике,
не работает — переносить в код вместе с текстом.
