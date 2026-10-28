# VIP QA: состав пакета кейса для AI (на согласование)

**Для:** Head of VIP, проверяющий QC
**Основание:** VIP QA Scorecard v1.6, раздел 14. До старта калибровки нужно «закрепить, что
эскалация подтверждается записью в Slack / notes, и согласовать состав пакета кейса».
**Статус:** проект от 28.10.2026, ждёт подписи.

## 1. Что AI получает сейчас по каждому кейсу

| Источник | Что именно | Откуда |
|---|---|---|
| Переписка | Все письма кейса: ветки одного игрока по одной теме, цепочка в 7 дней. Видны сообщения игрока, VIP-менеджера и других сотрудников с метками времени | Intercom, почтовый канал VIP |
| Внутренние заметки | Внутренние (жёлтые) заметки в ветках Intercom. Считаются системным следом действия | Intercom |
| Сегмент игрока | VIP / preVIP / regular и уровень лояльности (knight, baron, count, marquess, duke, prince…) | Атрибуты контакта Intercom (`Groups`, `Profitable`, `Status`), они синхронизируются из BO |
| Бренд | King Billy / Tomb Riches / Olympia | Intercom |
| Help Center бренда | Опубликованные правила: бонусы, вейджер, лимиты, сроки, KYC | Help Center |

Важно про сегмент: это значение на момент выгрузки, а не на дату кейса. Если игрока недавно
повысили, AI увидит новый сегмент.

## 2. Чего AI не видит, и что из-за этого происходит

Если доказательство для критерия лежит вне пакета, AI ставит **CANNOT DETERMINE**. Такой кейс
обязательно уходит на ручную проверку (Приложение Е). В Recall/Precision по этому критерию он
не входит и разбирается отдельно (раздел 14). FAIL «на всякий случай» запрещён.

| Критерий | Нужное доказательство (Приложение А) | Сейчас в пакете | Итог без доступа |
|---|---|---|---|
| crit-rg-commerce | Цитаты и timestamps; BO-статус SE / Closed; `vip_se_coff_dis_started` | Цитаты и timestamps | По переписке — да. Статус SE / Closed — CANNOT DETERMINE |
| crit-closure-breach | Timestamps, BO-статус и теги, Closure_Reopen, Jira | Timestamps | Выполнено ли закрытие — CANNOT DETERMINE |
| crit-lost-email-gate | Email_Change, BO-теги, ответы на квиз | Ответы на квиз, если они в переписке | Дубликат / перенос — CANNOT DETERMINE |
| crit-data-security, crit-abuse | Цитата | Да | Оценивается полностью |
| crit-payment-retention, crit-false-status | vip_cs, переписка | Переписка | Статус в vip_cs — CANNOT DETERMINE |
| crit-policy-promise | Цитата, BO | Цитата | Начисление в BO — CANNOT DETERMINE |
| crit-compliance-bypass | Цитата, Slack | Цитата | Slack — CANNOT DETERMINE |
| crit-records | Переписка против notes / Slack | Переписка, заметки Intercom | Запись вне Intercom — CANNOT DETERMINE |
| own-lost | Переписка, daily_report | Переписка | По переписке — да |
| pay-handoff, proc-handoff, proc-payments-context | vip_cs, timestamps vip_cs | Нет | CANNOT DETERMINE |
| action-missed, proc-route | Slack, notes, Jira, BO | Заметки Intercom | Есть заметка — PASS. Иначе CANNOT DETERMINE |
| own-info, q-accuracy-minor | Переписка и версия Wiki / T&C | Переписка и Help Center | Оценивается. Внутренние правила Wiki не видны |
| bonus-unjustified | Переписка, история бонусов, BO | Переписка и сегмент | Без истории бонусов — CANNOT DETERMINE, кроме давления, видного в переписке |
| q-clarity, q-expectations, q-effort, q-complaint | Переписка | Да | Оценивается полностью |
| proc-first-response, proc-next-response, proc-update, proc-resolution | Timestamps; vip_main (перерывы); daily_report | Timestamps | Без графика смен — не оценивается |
| proc-notes | notes / VIP table, Closure_Reopen, Email_Change, vip_main | Заметки Intercom | Отсутствие записи вне Intercom ничего не доказывает → CANNOT DETERMINE |
| proc-handover | daily_report | Нет | Не оценивается |
| proc-outbound | Переписка, notes, BO (state) | Переписка и сегмент | State (Churn 7–14 / 15–30 / SE / Closed) не виден |
| style-* | Переписка | Да | Оценивается полностью |
| Step 0: segment + state | BO | Сегмент — да | State — «Unknown», если его нет в переписке |

## 3. Что можно добавить в пакет, если дадите выгрузку

Каждый пункт переводит часть CANNOT DETERMINE в обычную оценку. Порядок — по пользе.

1. **Сообщения Slack vip_cs по кейсу** (ID игрока, время, текст). Закрывают pay-handoff,
   proc-handoff, proc-payments-context, action-missed, crit-payment-retention,
   crit-false-status.
2. **График смен менеджеров** (кто когда на смене, в UTC). Включает proc-first-response,
   proc-next-response и proc-handover. Это поле `shifts` в `config/vip_staff.yaml`.
3. **Статус и теги игрока из BO на дату кейса**: state, SE / Closed, `vip_se_coff_dis_started`.
   Закрывают crit-rg-commerce, crit-closure-breach и proc-outbound, а сегмент становится
   «на дату кейса».
4. **История бонусов игрока за 30 дней до кейса.** Закрывает bonus-unjustified и
   bonus_eligibility.
5. **Записи Closure_Reopen / Email_Change / VIP table по игроку.** Закрывают proc-notes,
   crit-lost-email-gate, crit-records.
6. **Jira: тикеты по ID игрока** (код, время, статус). Закрывают proc-route и action-missed.

Удобный формат — CSV или Google Sheet с ID игрока Intercom (или email) и временем события.
Ключ для связи — email игрока или ID контакта Intercom.

## 4. Доступы проверяющего (раздел 14, без них калибровку не начинаем)

Выдаёт Head of VIP до старта:

- [ ] Каналы: Intercom (KB, TB, PL-OL), почта VIP, WhatsApp / Telegram менеджеров, все
      VIP-каналы Slack
- [ ] Системы, просмотр: SoftSwiss BO (KB, Olympia, Playmax, TombRiches), Apuesta (TR), Jira
- [ ] Записи: notes / VIP table, Closure_Reopen, Email_Change, история бонусов
- [ ] Документация: вся VIP Wiki (Communication Flow, SLA & Monitoring, Manager Workflow,
      Bonus Flow) с историей версий за период

## 5. Выборка для калибровки

Собрана по правилам раздела 14 (`scripts/build_vip_s14_sample.py`, sample `vip-v16-s14-v1`,
seed 20261028).

- Период: кейсы, начатые 1–15 октября 2026.
- Найдено 73 сложных кейса (Payments 47, RG / закрытие 15, отказ с негативом 13,
  жалобы 9, high-value 8, потерянный follow-up 6) и 73 штатных случайных. Всего 146.
- **Не хватает до 150**, потому что почта VIP собрана только по 14 октября. Нужно дособрать
  15–16 октября, тогда выборка пересобирается тем же скриптом.
- Категорию «довольный игрок при ошибке» до оценки не определить, она появится после
  разметки.

## 6. На подпись

- [ ] Пакет кейса из раздела 1 принимается как достаточный для старта калибровки.
- [ ] Что из раздела 3 будет выгружено и к какой дате.
- [ ] Эскалация подтверждается только записью: внутренней заметкой в Intercom или сообщением в
      Slack. Фраза игроку «передал коллегам» без записи не считается.
- [ ] Доступы проверяющего из раздела 4 выданы.

Head of VIP: ____________________   Дата: __________
QC: ____________________   Дата: __________
