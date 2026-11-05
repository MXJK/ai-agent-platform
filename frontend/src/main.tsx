import { createRoot } from 'react-dom/client';
import { Workbench } from './app/Workbench';
import './styles/workbench.css';

const container = document.getElementById('root');
if (!container) throw new Error('Workbench mount point is missing');
const root = createRoot(container);
root.render(<Workbench />);

// Controller setup runs after the committed shell exists. React owns all view
// panels and registry lists; controller-owned islands never rerender through React.
let disposed = false;
let stop: (() => void) | undefined;
void import('./app/lifecycle').then(({ startWorkbench }) => {
  if (!disposed) stop = startWorkbench(container);
}).catch(error => {
  if (!disposed) {
    const region = document.getElementById('toast-region');
    if (region) region.textContent = `工作台加载失败，请刷新重试：${String(error)}`;
  }
});
if (import.meta.hot) import.meta.hot.dispose(() => { disposed = true; stop?.(); root.unmount(); });
