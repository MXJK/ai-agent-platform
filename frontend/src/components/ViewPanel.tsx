import { useSyncExternalStore, type ComponentProps } from 'react';
import { navigation, type View } from '../app/navigation';

type Props = ComponentProps<'section'> & { view: View };

/** Keep inactive panels mounted so drafts, focus targets and streaming nodes survive navigation. */
export function ViewPanel({ view, className = '', children, ...props }: Props) {
  const current = useSyncExternalStore(navigation.subscribe, navigation.getSnapshot);
  const active = current === view;
  return <section {...props} data-view-panel={view} hidden={!active}
    className={`${className}${active ? ' active' : ''}`}>{children}</section>;
}
