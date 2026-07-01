import { useState } from 'react';

interface ZipImage {
  path: string;
  name: string;
  size: number;
  is_product: boolean;
}

interface ZipImagePickerProps {
  uploadId: string;
  images: ZipImage[];
  selectedName: string;
  onSelect: (path: string) => void;
}

function ZipThumb({ uploadId, path, name }: { uploadId: string; path: string; name: string }) {
  const [failed, setFailed] = useState(false);
  const src = `/api/scan-preview/${encodeURIComponent(uploadId)}?path=${encodeURIComponent(path)}`;
  if (failed) return (
    <div style={{ width:'100%', aspectRatio:'1/1', display:'flex', alignItems:'center', justifyContent:'center', background:'var(--c-bg,#111)', borderRadius:4 }}>
      <span style={{ color:'var(--c-muted,#666)', fontSize:12 }}>?</span>
    </div>
  );
  return (
    <img src={src} alt={name} loading="lazy" decoding="async" onError={() => setFailed(true)}
      style={{ width:'100%', aspectRatio:'1/1', objectFit:'contain', background:'var(--c-bg,#111)', borderRadius:4, display:'block' }} />
  );
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function ZipImagePicker({ uploadId, images, selectedName, onSelect }: ZipImagePickerProps) {
  const [showAll, setShowAll] = useState(false);
  if (!images || images.length === 0) return null;

  const productImages = images.filter(i => i.is_product);
  const visible = showAll ? images : (productImages.length > 0 ? productImages : images.slice(0, 9));
  const isSelected = (img: ZipImage) => selectedName === img.path || selectedName === img.name;

  return (
    <div style={{ marginBottom:'1.25rem' }}>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:'0.5rem' }}>
        <span style={{ color:'var(--c-text,#e5e5e5)', fontSize:13, fontWeight:500 }}>
          Фото в архиве — нажми чтобы выбрать
        </span>
        {(images.length > visible.length || showAll) && (
          <button type="button" className="btn-link small" onClick={() => setShowAll(v => !v)}>
            {showAll ? 'Скрыть' : `Показать все (${images.length})`}
          </button>
        )}
      </div>

      <div style={{ display:'grid', gridTemplateColumns:'repeat(auto-fill, minmax(110px, 1fr))', gap:'0.5rem' }}>
        {visible.map(img => (
          <button
            key={img.path}
            type="button"
            title={img.path}
            onClick={() => onSelect(img.path)}
            style={{
              display:'flex', flexDirection:'column', alignItems:'center',
              gap:'0.25rem', padding:'0.4rem',
              border: isSelected(img) ? '2px solid var(--c-accent,#7c6fff)' : '1px solid var(--c-border,#333)',
              borderRadius:6,
              background: isSelected(img) ? 'color-mix(in srgb, var(--c-accent,#7c6fff) 12%, transparent)' : 'var(--c-surface,#1a1a1a)',
              cursor:'pointer', textAlign:'center', width:'100%',
            }}
          >
            <ZipThumb uploadId={uploadId} path={img.path} name={img.name} />
            <span style={{ fontSize:10, wordBreak:'break-all', lineHeight:1.2, color:'var(--c-text,#e5e5e5)', fontFamily:'monospace' }}>
              {img.name}
            </span>
            <span style={{ fontSize:10, color:'var(--c-muted,#666)' }}>{formatSize(img.size)}</span>
          </button>
        ))}
      </div>

      <p style={{ marginTop:'0.4rem', fontSize:12, color:'var(--c-muted,#666)' }}>
        Выбранное имя подставится в поле «Имя файла фото продукта» ниже.
      </p>
    </div>
  );
}
