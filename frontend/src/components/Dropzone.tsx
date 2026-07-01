import { useState, useRef, DragEvent, ChangeEvent } from 'react';

interface DropzoneProps {
  onFile?: (file: File) => void;
  file?: File | null;
  onFiles?: (files: File[]) => void;
  files?: File[];
  multiple?: boolean;
  accept?: string;
  disabled?: boolean;
}

export function Dropzone({
  onFile,
  file = null,
  onFiles,
  files = [],
  multiple = false,
  accept = '.zip',
  disabled = false,
}: DropzoneProps) {
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const activeFiles = multiple ? files : (file ? [file] : []);

  const pushFiles = (picked: FileList | File[]) => {
    const list = Array.from(picked);
    if (!list.length) return;
    if (multiple) {
      onFiles?.(list);
      return;
    }
    onFile?.(list[0]);
  };

  const handleDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragOver(false);
    if (disabled) return;
    pushFiles(e.dataTransfer.files);
  };

  const handleSelect = (e: ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) pushFiles(e.target.files);
  };

  const formatSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  };

  return (
    <div
      className={`dropzone ${dragOver ? 'dropzone-active' : ''} ${activeFiles.length ? 'dropzone-filled' : ''} ${disabled ? 'dropzone-disabled' : ''}`}
      onDragOver={(e) => { e.preventDefault(); if (!disabled) setDragOver(true); }}
      onDragLeave={() => setDragOver(false)}
      onDrop={handleDrop}
      onClick={() => !disabled && inputRef.current?.click()}
    >
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        multiple={multiple}
        onChange={handleSelect}
        style={{ display: 'none' }}
        disabled={disabled}
      />

      {activeFiles.length ? (
        <div className="dropzone-file">
          <div className="dropzone-icon-filled">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <polyline points="17 8 12 3 7 8" />
              <line x1="12" y1="3" x2="12" y2="15" />
            </svg>
          </div>
          <div className="dropzone-file-info">
            <div className="dropzone-file-name">
              {multiple ? `${activeFiles.length} ZIP-файлов выбрано` : activeFiles[0].name}
            </div>
            <div className="dropzone-file-size dim small">
              {multiple
                ? `${activeFiles.slice(0, 3).map((f) => f.name).join(', ')}${activeFiles.length > 3 ? ' ...' : ''} · клик для замены`
                : `${formatSize(activeFiles[0].size)} · клик для замены`}
            </div>
          </div>
        </div>
      ) : (
        <>
          <div className="dropzone-icon">
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <polyline points="17 8 12 3 7 8" />
              <line x1="12" y1="3" x2="12" y2="15" />
            </svg>
          </div>
          <div className="dropzone-text">{multiple ? 'Перетащи ZIP-архивы сюда' : 'Перетащи ZIP-архив сюда'}</div>
          <div className="dropzone-hint dim small">
            {multiple ? 'или клик для выбора нескольких файлов' : 'или клик для выбора файла'}
          </div>
        </>
      )}
    </div>
  );
}
