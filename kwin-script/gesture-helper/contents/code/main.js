let lastMinimized = null;

function gestureMinimizeActive() {
    let w = workspace.activeWindow;
    if (w) {
        lastMinimized = w;
        w.minimized = true;
    }
}

function gestureRestoreLastMinimized() {
    if (lastMinimized) {
        lastMinimized.minimized = false;
        workspace.activeWindow = lastMinimized;
        lastMinimized = null;
    }
}

registerShortcut("GestureMinimizeActive", "Gesture: Minimize Active Window", "", gestureMinimizeActive);
registerShortcut("GestureRestoreLastMinimized", "Gesture: Restore Last Minimized Window", "", gestureRestoreLastMinimized);
