import { useState } from 'react';

const SECTIONS = [
  {
    id: 'scan-adapt',
    label: 'Scan + Adapt',
    content: `
**Что делает:** Адаптирует оффер под новое ГЕО — меняет продукт, цены, скрытые поля формы и фото.

**Шаг 1 — Загрузи ZIP и просканируй**
Загрузи ZIP-архив оффера. Сканер найдёт: название продукта, цены, валюту, фото, языки и поля формы.

**Шаг 2 — Заполни параметры**
- **Целевое ГЕО** — код страны (например: DE, PL, HU) или валюта
- **Продукт** — что заменить и на что
- **Цены** — новая и старая цена (подставятся в форму и HTML)
- **Вертикаль / exclude_word** — для скрытых полей формы
- **Фото** — замени product.png, product2.png и т.д. из своей базы

**Шаг 3 — Скачай результат**
Готовый адаптированный ZIP можно скачать.

**Совет:** Можно обработать несколько архивов подряд — они встанут в очередь.
    `,
  },
  {
    id: 'clean',
    label: 'Clean',
    content: `
**Что делает:** Очищает оффер от чужого кода — удаляет все скрипты и пиксели партнёрской программы, SESSION-блоки с трекингом, UTM hidden-инпуты, мусорные редиректы и прочий вредоносный/лишний код который ПП вшивают в свои офферы.

**Что проверить после очистки:** 
- **Не все инпуты убираются**
- **Некоторые скрипты не до конца могу очиститься** — так как постоянно имеют разные переменные


**Результат:** Чистый ZIP готовый к инжекту или заливке.
  `,
  },
  {
    id: 'inject',
    label: 'Inject',
    content: `
**Что делает:** Вставляет нужные скрипты и настройки в оффер — форму заказа, Keitaro API, пиксели.

**Что вставляется:**
- **api.php** — всегда заменяется на рабочий шаблон с Keitaro Orders API
- **index.php** — всегда заменяется на php файл, вставляются все актуальные скрипты
- **Форма заказа** — ставятся нужные инпуты, type, required и тд.
- **Скрипты** — вставляются в HEAD или перед </body>

**Совет:** Inject работает поверх уже очищенного оффера. Используй Clean → Inject по очереди или Clean + Inject за один шаг.
  `,
  },
  {
    id: 'clean-inject',
    label: 'Clean + Inject',
    content: `
**Что делает:** Полный пайплайн за один шаг: сначала Clean, потом Inject.

**Когда использовать:** Перед заливом в Keitaro — для любых офферов, как с ПП напрямую, так и тех что присылают байеры. Это стандартный шаг перед каждой заливкой.

**Порядок обработки:**
1. Удаляются все чужие пиксели, SESSION-блоки, скрипты
2. Очищается action="" форм
3. Вставляются нужные параметры в форму/виджет/маску
4. Подключается форма к Keitaro
5. Добавляются нужные скрипты/пиксели

**Результат:** Готовый к заливке оффер одним кликом.
  `,
  },
  {
    id: 'optimize',
    label: 'Optimize',
    content: `
**Что делает:** Конвертирует все PNG/JPG/JPEG изображения в WebP, сжимает их и обновляет пути в HTML, PHP и CSS.

**Зачем:**
1. Фотки сжимаются в 2-5 раз без потери видимого качества
2. Конвертация в WebP — современный формат, браузеры загружают быстрее
3. Размер оффера уменьшается в 2-3 раза (например 25MB → 10MB)
4. Страница грузится быстрее — лучше конверсия

**Флоу:**
1. Загрузи ZIP-архив
2. Нажми "Просканировать" — увидишь список картинок и ожидаемую экономию
3. Нажми "Конвертировать" — скачай готовый ZIP

**Что НЕ трогается:** Стили в CSS остаются без изменений (пути обновляются, но сам код не меняется). GIF, видео, SVG, шрифты и прочие файлы — не конвертируются и не изменяются.

**Совет:** Запускай Optimize после Clean + Inject — это финальный шаг перед заливкой. Иногда фотки могут потерять фон, в таком случае, либо вручную конвертировать, либо оставить ориг.
  `,
  }
];

function renderMarkdown(text: string) {
  return text
    .trim()
    .split('\n')
    .map((line, i) => {
      if (line.startsWith('**') && line.endsWith('**') && line.indexOf('**', 2) === line.length - 2) {
        return <p key={i} style={{ fontWeight: 700, color: 'var(--text)', marginBottom: 4, marginTop: 12 }}>{line.slice(2, -2)}</p>;
      }
      // Inline bold
      const parts = line.split(/(\*\*[^*]+\*\*)/g);
      const rendered = parts.map((part, j) =>
        part.startsWith('**') && part.endsWith('**')
          ? <strong key={j}>{part.slice(2, -2)}</strong>
          : part
      );
      if (line.startsWith('- ')) {
        return <li key={i} style={{ marginBottom: 3, color: 'var(--text-muted)' }}>{rendered.slice(1)}</li>;
      }
      if (line === '') return <br key={i} />;
      return <p key={i} style={{ margin: '3px 0', color: 'var(--text-muted)', lineHeight: 1.5 }}>{rendered}</p>;
    });
}

export function HelpWidget() {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState('scan-adapt');

  const section = SECTIONS.find(s => s.id === active)!;

  return (
    <>
      {/* Кнопка */}
      <button
        onClick={() => setOpen(o => !o)}
        title="Инструкция"
        style={{
          position: 'fixed',
          bottom: 28,
          right: 28,
          width: 48,
          height: 48,
          borderRadius: '50%',
          background: 'var(--accent)',
          border: 'none',
          cursor: 'pointer',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontSize: 22,
          color: '#fff',
          boxShadow: '0 4px 16px rgba(124,108,241,0.45)',
          zIndex: 1000,
          transition: 'transform 0.15s, background 0.15s',
        }}
        onMouseEnter={e => (e.currentTarget.style.transform = 'scale(1.1)')}
        onMouseLeave={e => (e.currentTarget.style.transform = 'scale(1)')}
      >
        {open ? '✕' : '?'}
      </button>

      {/* Оверлей */}
      {open && (
        <div
          onClick={() => setOpen(false)}
          style={{
            position: 'fixed', inset: 0,
            background: 'rgba(0,0,0,0.5)',
            zIndex: 999,
          }}
        />
      )}

      {/* Попап */}
      {open && (
        <div
          style={{
            position: 'fixed',
            bottom: 88,
            right: 28,
            width: 560,
            maxHeight: '75vh',
            background: 'var(--bg-elevated)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius-lg)',
            boxShadow: '0 8px 40px rgba(0,0,0,0.4)',
            zIndex: 1001,
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
          }}
        >
          {/* Хедер */}
          <div style={{
            padding: '16px 20px 12px',
            borderBottom: '1px solid var(--border)',
            flexShrink: 0,
          }}>
            <div style={{ fontWeight: 700, fontSize: 16, color: 'var(--text)', marginBottom: 10 }}>
              Инструкция по разделам
            </div>
            {/* Табы */}
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {SECTIONS.map(s => (
                <button
                  key={s.id}
                  onClick={() => setActive(s.id)}
                  style={{
                    padding: '4px 10px',
                    borderRadius: 6,
                    border: '1px solid',
                    borderColor: active === s.id ? 'var(--accent)' : 'var(--border)',
                    background: active === s.id ? 'var(--accent-soft)' : 'transparent',
                    color: active === s.id ? 'var(--accent)' : 'var(--text-muted)',
                    cursor: 'pointer',
                    fontSize: 13,
                    fontWeight: active === s.id ? 600 : 400,
                    transition: 'all 0.15s',
                  }}
                >
                  {s.label}
                </button>
              ))}
            </div>
          </div>

          {/* Контент */}
          <div style={{
            padding: '16px 20px',
            overflowY: 'auto',
            flexGrow: 1,
          }}>
            <div style={{ fontSize: 14 }}>
              <ul style={{ paddingLeft: 18, margin: 0 }}>
                {renderMarkdown(section.content)}
              </ul>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
