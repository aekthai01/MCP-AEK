'use strict';
try {
  const mode = localStorage.getItem('aek-theme');
  if (mode === 'dark' || mode === 'light') {
    document.documentElement.dataset.theme = mode;
    document.querySelector('meta[name="theme-color"]').content = mode === 'dark' ? '#101214' : '#f4f3ef';
  }
} catch (_) { /* Private storage may be unavailable. System CSS remains usable. */ }
