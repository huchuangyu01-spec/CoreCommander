# -*- coding: utf-8 -*-
import math
import time
import random
from PySide6.QtCore import Qt, QRectF, QPoint, Signal, QTimer
from PySide6.QtWidgets import QWidget, QMenu
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QFont, QAction
from core_commander.core.macro_manager import MacroAction

class UIBlockItem:
    """Represents a merged high-level duration block displayed on the horizontal timeline."""
    def __init__(self, block_id, start_time, duration, type_str, key_code=0, key_name="", x=0, y=0, path_points=None, track_index=0):
        self.block_id = block_id
        self.start_time = start_time        # start offset (ms)
        self.duration = duration            # duration (ms)
        self.type_str = type_str            # "keyboard", "mouse_click", "mouse_move"
        self.key_code = key_code            # vkCode for keys or mouse button
        self.key_name = key_name
        self.x = x                          # X coordinate (for mouse click)
        self.y = y                          # Y coordinate (for mouse click)
        # List of coordinates (rel_time_ms, cx, cy) within a continuous mouse path
        self.path_points = path_points if path_points is not None else []
        self.track_index = track_index


class TimelineWidget(QWidget):
    """
    Custom QWidget displaying a professional horizontal editor with multi-tracks:
    Track 0: Keyboard Actions
    Track 1: Mouse Actions
    Supports horizontal zooming, click playhead seek, block dragging, and right-edge duration stretching.
    """
    selectionChanged = Signal(UIBlockItem) # Emitted when a block is selected or deselected (None)
    blocksChanged = Signal()               # Emitted when blocks are updated/dragged by user
    multiSelectionChanged = Signal(list)   # Emitted when multiple blocks are selected via marquee selection
    blockDoubleClicked = Signal(UIBlockItem) # Emitted when a block is double-clicked
    scaleChanged = Signal(float)
    scrollOffsetChanged = Signal(float)
    playheadChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(250)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.undo_stack = []
        
        # Scaling configurations: how many milliseconds represent 1 pixel
        # Default: 1 pixel = 10 ms (i.e. 100 pixels = 1000 ms = 1 second)
        self.scale = 10.0 
        
        # Timeline Ruler & Tracks Geometry
        self.ruler_height = 25
        self.track_height = 35
        self.track_padding = 0
        self.num_tracks = 6 # 4 keyboard (0-3), 2 mouse (4-5)
        
        # Dynamic State variables
        self.blocks = [] # List of UIBlockItem
        self.selected_block = None
        self.selected_blocks = [] # List of multi-selected blocks
        self.marquee_selection_active = False
        from PySide6.QtCore import QPointF
        self.marquee_start_pos = QPointF(0, 0)
        self.marquee_current_pos = QPointF(0, 0)
        self.playhead_ms = 0
        
        # Snapping state
        self.snap_line_ms = None
        self.snap_opacity = 0.0
        
        # Dragging variables
        self.dragged_block = None
        self.drag_mode = None # "move" or "resize"
        self.drag_start_x = 0
        self.drag_start_time = 0
        self.drag_start_duration = 0
        
        # Scrolling variables
        self.scroll_offset_x = 0.0 # horizontal offset in pixels
        self.scrolling = False
        self.scroll_drag_start_x = 0
        self.scroll_drag_start_offset = 0.0
        
        # Clipboard buffer for copy-paste operations
        self.clipboard_block = None
        self.right_clicked_time = 0
        
        # Timeline width bounds
        self.timeline_max_ms = 10000 # default 10 seconds boundary
        
        # Auto-scrolling
        self.auto_scroll_timer = QTimer(self)
        self.auto_scroll_timer.timeout.connect(self.on_auto_scroll)
        self.auto_scroll_dir = 0
        
        self.blocksChanged.connect(self.recalculate_bounds)

    def recalculate_bounds(self):
        """Recalculate the timeline maximum time boundary based on blocks."""
        max_time = 5000
        for b in self.blocks:
            max_time = max(max_time, b.start_time + b.duration)
        self.timeline_max_ms = max_time + 2000
        self.update()

    def cleanup_widget(self):
        """Cleanup timers and signal slots to prevent memory leaks."""
        if hasattr(self, "auto_scroll_timer") and self.auto_scroll_timer:
            try:
                self.auto_scroll_timer.stop()
                self.auto_scroll_timer.timeout.disconnect()
            except Exception:
                pass
        if hasattr(self, "snap_timer") and self.snap_timer:
            try:
                self.snap_timer.stop()
                self.snap_timer.timeout.disconnect()
            except Exception:
                pass

    def set_scale(self, ms_per_pixel):
        """Update scale dynamically (zoom in/out)."""
        self.scale = max(1.0, float(ms_per_pixel))
        self.update()
        self.scaleChanged.emit(self.scale)

    def set_playhead(self, ms):
        """Update playhead (red line) position."""
        self.playhead_ms = max(0, ms)
        self.update()
        self.playheadChanged.emit(self.playhead_ms)

    def set_scroll_offset(self, val):
        self.scroll_offset_x = val
        self.update()
        self.scrollOffsetChanged.emit(self.scroll_offset_x)

    def push_undo_state(self):
        """Saves a deep copy of the current blocks state to the undo stack."""
        state = []
        for b in self.blocks:
            cloned = UIBlockItem(
                block_id=b.block_id,
                start_time=b.start_time,
                duration=b.duration,
                type_str=b.type_str,
                key_code=b.key_code,
                key_name=b.key_name,
                x=b.x,
                y=b.y,
                path_points=list(b.path_points),
                track_index=b.track_index
            )
            state.append(cloned)
        self.undo_stack.append(state)
        if len(self.undo_stack) > 50:
            self.undo_stack.pop(0)

    def undo(self):
        """Restores the previous blocks state from the undo stack."""
        if not self.undo_stack:
            return
        state = self.undo_stack.pop()
        self.blocks = state
        self.selected_block = None
        self.selected_blocks = []
        self.update()
        self.blocksChanged.emit()

    # --- Actions to UI Blocks Converter ---
    def set_actions(self, actions):
        """Merges a list of sequential MacroActions into UIBlockItems."""
        self.blocks.clear()
        self.selected_block = None
        
        if not actions:
            self.update()
            return
 
        # Sort actions to make sure order is clean
        sorted_acts = sorted(actions, key=lambda a: a.time_ms)
        
        # Pre-process frame actions if present
        has_frames = any(act.event_type == "frame" for act in sorted_acts)
        if has_frames:
            translated_actions = []
            previous_active_keys = set()
            previous_x, previous_y = 0, 0
            
            # Find first frame coordinates to initialize cursor pos
            first_frame = next((act for act in sorted_acts if act.event_type == "frame"), None)
            if first_frame:
                previous_x, previous_y = first_frame.x, first_frame.y
                
            for act in sorted_acts:
                if act.event_type != "frame":
                    # Keep traditional actions if mixed in
                    translated_actions.append(act)
                    continue
                current_active_keys = set(act.active_keys)
                
                # Key downs (in current, not in previous)
                for vk in current_active_keys - previous_active_keys:
                    event_type = "mouse_down" if vk in (0x01, 0x02, 0x04, 0x05, 0x06) else "key_down"
                    from core_commander.core.input_hook import VK_NAMES
                    name = VK_NAMES.get(vk, f"Key {vk}") if event_type == "key_down" else VK_NAMES.get(vk, f"Mouse Button {vk}")
                    translated_actions.append(MacroAction(act.time_ms, event_type, key_code=vk, key_name=name, x=act.x, y=act.y))
                
                # Key ups (in previous, not in current)
                for vk in previous_active_keys - current_active_keys:
                    event_type = "mouse_up" if vk in (0x01, 0x02, 0x04, 0x05, 0x06) else "key_up"
                    from core_commander.core.input_hook import VK_NAMES
                    name = VK_NAMES.get(vk, f"Key {vk}") if event_type in ("key_down", "key_up") else VK_NAMES.get(vk, f"Mouse Button {vk}")
                    translated_actions.append(MacroAction(act.time_ms, event_type, key_code=vk, key_name=name, x=act.x, y=act.y))
                
                # Mouse move
                if act.x != previous_x or act.y != previous_y:
                    translated_actions.append(MacroAction(act.time_ms, "mouse_move", x=act.x, y=act.y))
                    
                previous_active_keys = current_active_keys
                previous_x, previous_y = act.x, act.y
                
            # Clean up remaining unreleased keys at the end
            for vk in previous_active_keys:
                event_type = "mouse_up" if vk in (0x01, 0x02, 0x04, 0x05, 0x06) else "key_up"
                from core_commander.core.input_hook import VK_NAMES
                name = VK_NAMES.get(vk, f"Key {vk}") if event_type in ("key_down", "key_up") else VK_NAMES.get(vk, f"Mouse Button {vk}")
                last_time = sorted_acts[-1].time_ms if sorted_acts else 1000
                translated_actions.append(MacroAction(last_time, event_type, key_code=vk, key_name=name, x=previous_x, y=previous_y))
                
            sorted_acts = sorted(translated_actions, key=lambda a: a.time_ms)

        # Temporary buffers to match downs with ups
        unmatched_kb = {} # vkCode -> (down_action, index)
        unmatched_ms_click = {} # btn_code -> (down_action, index)
        
        # Temporary buffer for continuous mouse move segments
        current_mouse_move_points = []
        mouse_move_start_time = 0
        
        block_counter = 0

        def flush_mouse_move_block():
            nonlocal block_counter
            if not current_mouse_move_points:
                return
            # Determine start, duration
            start = mouse_move_start_time
            end = current_mouse_move_points[-1][0]
            duration = max(20, end - start)
            
            # Map absolute time offsets into relative offsets inside path_points
            rel_points = []
            for t_abs, cx, cy in current_mouse_move_points:
                rel_points.append((t_abs - start, cx, cy))
                
            block_id = f"block_{block_counter}"
            block_counter += 1
            
            # Use final coordinates as base fallback X, Y
            fx, fy = current_mouse_move_points[-1][1], current_mouse_move_points[-1][2]
            
            self.blocks.append(UIBlockItem(
                block_id=block_id,
                start_time=start,
                duration=duration,
                type_str="mouse_move",
                x=fx,
                y=fy,
                path_points=rel_points
            ))
            current_mouse_move_points.clear()

        # Parse loop
        for act in sorted_acts:
            # Only flush mouse move segment if the gap between consecutive mouse movements exceeds 300ms.
            if act.event_type == "mouse_move":
                if current_mouse_move_points and (act.time_ms - current_mouse_move_points[-1][0] > 300):
                    flush_mouse_move_block()
                
            if act.event_type == "key_down":
                unmatched_kb[act.key_code] = (act, block_counter)
                block_counter += 1
            elif act.event_type == "key_up":
                if act.key_code in unmatched_kb:
                    down_act, bid = unmatched_kb.pop(act.key_code)
                    duration = max(10, act.time_ms - down_act.time_ms)
                    self.blocks.append(UIBlockItem(
                        block_id=f"kb_{bid}",
                        start_time=down_act.time_ms,
                        duration=duration,
                        type_str="keyboard",
                        key_code=act.key_code,
                        key_name=act.key_name
                    ))
                else:
                    # Lone keyup, create brief default block
                    self.blocks.append(UIBlockItem(
                        block_id=f"kb_{block_counter}",
                        start_time=max(0, act.time_ms - 100),
                        duration=100,
                        type_str="keyboard",
                        key_code=act.key_code,
                        key_name=act.key_name
                    ))
                    block_counter += 1
                    
            elif act.event_type == "mouse_down":
                unmatched_ms_click[act.key_code] = (act, block_counter)
                block_counter += 1
            elif act.event_type == "mouse_up":
                if act.key_code in unmatched_ms_click:
                    down_act, bid = unmatched_ms_click.pop(act.key_code)
                    duration = max(10, act.time_ms - down_act.time_ms)
                    self.blocks.append(UIBlockItem(
                        block_id=f"ms_click_{bid}",
                        start_time=down_act.time_ms,
                        duration=duration,
                        type_str="mouse_click",
                        key_code=act.key_code,
                        key_name=act.key_name,
                        x=down_act.x,
                        y=down_act.y
                    ))
                else:
                    # Lone mouse up
                    self.blocks.append(UIBlockItem(
                        block_id=f"ms_click_{block_counter}",
                        start_time=max(0, act.time_ms - 100),
                        duration=100,
                        type_str="mouse_click",
                        key_code=act.key_code,
                        key_name=act.key_name,
                        x=act.x,
                        y=act.y
                    ))
                    block_counter += 1
                    
            elif act.event_type == "mouse_move":
                if not current_mouse_move_points:
                    mouse_move_start_time = act.time_ms
                current_mouse_move_points.append((act.time_ms, act.x, act.y))
                
        # Flush remainder
        flush_mouse_move_block()
        
        # Flush unmatched downs
        for vk, (down_act, bid) in unmatched_kb.items():
            self.blocks.append(UIBlockItem(
                block_id=f"kb_{bid}",
                start_time=down_act.time_ms,
                duration=100,
                type_str="keyboard",
                key_code=down_act.key_code,
                key_name=down_act.key_name
            ))
        for btn, (down_act, bid) in unmatched_ms_click.items():
            self.blocks.append(UIBlockItem(
                block_id=f"ms_click_{bid}",
                start_time=down_act.time_ms,
                duration=100,
                type_str="mouse_click",
                key_code=down_act.key_code,
                key_name=down_act.key_name,
                x=down_act.x,
                y=down_act.y
            ))
            
        # Re-sort blocks list chronologically
        self.blocks.sort(key=lambda b: b.start_time)
        
        # Greedy Interval Assignment for Multi-Track auto layout with smart same-key same-track positioning
        # Keyboard Tracks: 0, 1, 2, 3
        kb_ends = [0, 0, 0, 0]
        track_last_block = [None, None, None, None]
        key_to_track = {}
        
        for b in self.blocks:
            if b.type_str == "keyboard":
                vk = b.key_code
                assigned_track = None
                
                # Prioritize previous track mapped to this key code unless hijacked by another key currently overlapping
                if vk in key_to_track:
                    candidate = key_to_track[vk]
                    last_b = track_last_block[candidate]
                    if last_b is not None and last_b.key_code != vk and b.start_time < kb_ends[candidate]:
                        pass # Candidate track is occupied by another key right now, search for another track
                    else:
                        assigned_track = candidate
                        
                if assigned_track is None:
                    # Look for first free track
                    free_tracks = [i for i in range(4) if b.start_time >= kb_ends[i]]
                    if free_tracks:
                        assigned_track = free_tracks[0]
                    else:
                        # Fallback to the track with the earliest end time to minimize overlap
                        assigned_track = min(range(4), key=lambda i: kb_ends[i])
                    key_to_track[vk] = assigned_track
                
                # Resolve overlap on the assigned track (truncate previous block on that track to create a clean sequential flow)
                prev_b = track_last_block[assigned_track]
                if prev_b is not None and b.start_time < kb_ends[assigned_track]:
                    prev_b.duration = max(10, b.start_time - prev_b.start_time)
                    kb_ends[assigned_track] = prev_b.start_time + prev_b.duration
                    
                b.track_index = assigned_track
                kb_ends[assigned_track] = b.start_time + b.duration
                track_last_block[assigned_track] = b
            elif b.type_str == "mouse_click":
                b.track_index = 4
            elif b.type_str == "mouse_move":
                b.track_index = 5
        
        # Calculate dynamic timeline boundary
        max_time = 5000
        for b in self.blocks:
            max_time = max(max_time, b.start_time + b.duration)
        self.timeline_max_ms = max_time + 2000

        # Force clamp track index categories to prevent any overrun bugs
        for b in self.blocks:
            if b.type_str == "keyboard":
                b.track_index = max(0, min(3, b.track_index))
            elif b.type_str == "mouse_click":
                b.track_index = 4
            elif b.type_str == "mouse_move":
                b.track_index = 5
                
        self.update()

    # --- UI Blocks back to MacroActions parser ---
    def get_actions(self, record_mode="event"):
        """Reconstructs linear MacroAction stream from UIBlockItems."""
        actions = []
        for b in self.blocks:
            if b.type_str == "keyboard":
                actions.append(MacroAction(b.start_time, "key_down", key_code=b.key_code, key_name=b.key_name))
                actions.append(MacroAction(b.start_time + b.duration, "key_up", key_code=b.key_code, key_name=b.key_name))
            elif b.type_str == "mouse_click":
                actions.append(MacroAction(b.start_time, "mouse_down", key_code=b.key_code, key_name=b.key_name, x=b.x, y=b.y))
                actions.append(MacroAction(b.start_time + b.duration, "mouse_up", key_code=b.key_code, key_name=b.key_name, x=b.x, y=b.y))
            elif b.type_str == "mouse_move":
                if not b.path_points:
                    # Fallback single point move
                    actions.append(MacroAction(b.start_time, "mouse_move", x=b.x, y=b.y))
                else:
                    # Calculate new scaling factor if duration stretched
                    original_duration = b.path_points[-1][0] if b.path_points[-1][0] > 0 else 1
                    scale_factor = float(b.duration) / float(original_duration)
                    
                    for rel_t, cx, cy in b.path_points:
                        new_rel_t = int(rel_t * scale_factor)
                        actions.append(MacroAction(b.start_time + new_rel_t, "mouse_move", x=cx, y=cy))
                        
        # Sort chronologically to preserve execution ordering
        actions.sort(key=lambda a: a.time_ms)
        
        if record_mode == "frame":
            if not actions:
                return []
            
            # Reconstruct mouse position timeline
            mouse_timeline = []
            for b in self.blocks:
                if b.type_str == "mouse_click":
                    mouse_timeline.append((b.start_time, b.x, b.y))
                    mouse_timeline.append((b.start_time + b.duration, b.x, b.y))
                elif b.type_str == "mouse_move":
                    if b.path_points:
                        original_duration = b.path_points[-1][0] if b.path_points[-1][0] > 0 else 1
                        scale_factor = float(b.duration) / float(original_duration)
                        for rel_t_orig, cx, cy in b.path_points:
                            mouse_timeline.append((b.start_time + int(rel_t_orig * scale_factor), cx, cy))
                    else:
                        mouse_timeline.append((b.start_time, b.x, b.y))
                        mouse_timeline.append((b.start_time + b.duration, b.x, b.y))
            
            if not mouse_timeline:
                mouse_timeline.append((0, 0, 0))
                
            mouse_timeline.sort(key=lambda item: item[0])
            
            def get_mouse_pos_at(t):
                if t <= mouse_timeline[0][0]:
                    return mouse_timeline[0][1], mouse_timeline[0][2]
                if t >= mouse_timeline[-1][0]:
                    return mouse_timeline[-1][1], mouse_timeline[-1][2]
                for idx in range(len(mouse_timeline) - 1):
                    t0, x0, y0 = mouse_timeline[idx]
                    t1, x1, y1 = mouse_timeline[idx + 1]
                    if t0 <= t <= t1:
                        if t0 == t1:
                            return x0, y0
                        ratio = (t - t0) / (t1 - t0)
                        return int(x0 + ratio * (x1 - x0)), int(y0 + ratio * (y1 - y0))
                return 0, 0

            frames = []
            max_time = max(act.time_ms for act in actions) if actions else 0
            for t in range(0, max_time + 10, 10):
                active_keys = set()
                for b in self.blocks:
                    if b.type_str in ("keyboard", "mouse_click"):
                        if b.start_time <= t < b.start_time + b.duration:
                            active_keys.add(b.key_code)
                mx, my = get_mouse_pos_at(t)
                frames.append(MacroAction(
                    time_ms=t,
                    event_type="frame",
                    x=mx,
                    y=my,
                    active_keys=list(active_keys)
                ))
            return frames
            
        return actions

    # --- Hit Testing Helper ---
    def _get_block_rect(self, block):
        x = (block.start_time / self.scale) - self.scroll_offset_x
        w = block.duration / self.scale
        w = max(4, w) # Minimum size mapping to avoid invisible 0ms blocks
        
        # Prevent overlapping with the next block on the same track at high zoom-out levels
        try:
            idx = self.blocks.index(block)
            for i in range(idx + 1, len(self.blocks)):
                if self.blocks[i].track_index == block.track_index:
                    max_w_pixels = (self.blocks[i].start_time - block.start_time) / self.scale
                    if max_w_pixels > 0:
                        # Allow it to shrink down to at least 1 pixel, but not exceed the next block's start point
                        w = min(w, max(1.0, max_w_pixels))
                    break
        except ValueError:
            pass
            
        y = self.ruler_height + block.track_index * self.track_height
        h = self.track_height - self.track_padding
        return QRectF(x, y, w, h)

    def _hit_test(self, point):
        """Returns (UIBlockItem, 'body'/'left_edge'/'right_edge'/None) at current point."""
        mx, my = point.x(), point.y()
        
        # Avoid clicking on the bottom scrollbar track area
        if my >= self.height() - 14:
            return None, None
            
        # Test blocks in reverse order so newer overlapping items are clicked first
        for b in reversed(self.blocks):
            rect = self._get_block_rect(b)
            if rect.contains(mx, my):
                # If mouse is within 6 pixels of the left boundary edge, it's a left resize trigger
                if abs(mx - rect.left()) <= 6:
                    return b, "left_edge"
                # If mouse is within 6 pixels of the right boundary edge, it's a right resize trigger
                if abs(mx - rect.right()) <= 6:
                    return b, "right_edge"
                return b, "body"
        return None, None

    # --- Snapping & Canvas Interaction Helpers ---
    def _trigger_snap_animation(self):
        if not hasattr(self, "snap_opacity"):
            self.snap_opacity = 0.0
        self.snap_opacity = 255.0
        if not hasattr(self, "snap_timer"):
            self.snap_timer = QTimer(self)
            self.snap_timer.setInterval(30)
            self.snap_timer.timeout.connect(self._on_snap_fade)
        if not self.snap_timer.isActive():
            self.snap_timer.start()

    def _on_snap_fade(self):
        if hasattr(self, "snap_opacity") and self.snap_opacity > 0:
            self.snap_opacity = max(0.0, self.snap_opacity - 25)
            self.update()
        else:
            if hasattr(self, "snap_timer") and self.snap_timer.isActive():
                self.snap_timer.stop()

    def _get_track_drag_limits(self, block, target_track, exclude_selected=False):
        """
        Scans other blocks on the target track to determine the left and right limits 
        (in milliseconds) for the current block to prevent any time overlap.
        Returns (left_limit, right_limit).
        """
        left_limit = 0
        right_limit = 9999999
        
        exclude_ids = {b.block_id for b in self.selected_blocks} if exclude_selected else {block.block_id}
        
        for other in self.blocks:
            if other.block_id in exclude_ids or other.track_index != target_track:
                continue
            
            # Use original sorted order to find the immediate neighbors
            if other.start_time <= block.start_time:
                left_limit = max(left_limit, other.start_time + other.duration)
            else:
                right_limit = min(right_limit, other.start_time)
                
        return left_limit, right_limit

    def _find_non_overlapping_position(self, type_str, start_time, duration, preferred_track=None):
        """
        Finds a non-overlapping track index and start time for a block of given type.
        Tries the preferred_track (clamped to correct range) first at start_time.
        If overlapping, tries other tracks in the same category.
        If all tracks in the category overlap, it scans forward in 10ms steps until a spot is found.
        Returns (final_start_time, final_track_index).
        """
        if type_str == "keyboard":
            tracks = [0, 1, 2, 3]
        elif type_str == "mouse_click":
            tracks = [4]
        else:
            tracks = [5]
            
        if preferred_track is not None:
            if type_str == "keyboard":
                pref = max(0, min(3, preferred_track))
            elif type_str == "mouse_click":
                pref = 4
            else:
                pref = 5
            search_tracks = [pref] + [t for t in tracks if t != pref]
        else:
            search_tracks = tracks
            
        curr_start = round(start_time / 10.0) * 10
        
        def has_overlap(s, d, t):
            for b in self.blocks:
                if b.track_index == t:
                    if not (s + d <= b.start_time or s >= b.start_time + b.duration):
                        return True
            return False
            
        while True:
            for t in search_tracks:
                if not has_overlap(curr_start, duration, t):
                    return curr_start, t
            curr_start += 10

    def _apply_snapping(self, block, target_start, target_dur, drag_type):
        snap_threshold_ms = max(10.0, 8.0 * self.scale)
        self.snap_line_ms = None
        target_end = target_start + target_dur
        best_snap_diff = snap_threshold_ms + 1
        best_snap_val = None
        best_snap_type = None
        
        for other in self.blocks:
            if other.block_id == block.block_id:
                continue
            other_start = other.start_time
            other_end = other.start_time + other.duration
            
            for ref_val in (other_start, other_end):
                if drag_type in ("body", "left_edge"):
                    diff = abs(target_start - ref_val)
                    if diff < best_snap_diff:
                        best_snap_diff = diff
                        best_snap_val = ref_val
                        best_snap_type = "start"
                if drag_type in ("body", "right_edge"):
                    diff = abs(target_end - ref_val)
                    if diff < best_snap_diff:
                        best_snap_diff = diff
                        best_snap_val = ref_val
                        best_snap_type = "end"
                        
        if best_snap_val is not None and best_snap_diff <= snap_threshold_ms:
            if drag_type == "body":
                if best_snap_type == "start":
                    self.snap_line_ms = best_snap_val
                    self._trigger_snap_animation()
                    return best_snap_val
                else:
                    self.snap_line_ms = best_snap_val
                    self._trigger_snap_animation()
                    return best_snap_val - target_dur
            elif drag_type == "left_edge":
                self.snap_line_ms = best_snap_val
                self._trigger_snap_animation()
                return best_snap_val
            elif drag_type == "right_edge":
                self.snap_line_ms = best_snap_val
                self._trigger_snap_animation()
                return best_snap_val - target_start
                
        return target_start if drag_type != "right_edge" else target_dur

    # --- Mouse Event Handlers ---
    def mouseDoubleClickEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
            
        mx, my = event.position().x(), event.position().y()
        h = self.height()
        w = self.width()
        
        # Avoid scrollbar area
        w_total = max(float(w), self.timeline_max_ms / self.scale)
        if w_total > w and my >= h - 14:
            event.accept()
            return
            
        # Avoid ruler area
        if my <= self.ruler_height:
            event.accept()
            return
            
        # Check if we clicked on a block (only add block on blank space)
        hit_block, part = self._hit_test(event.position())
        if hit_block:
            self.blockDoubleClicked.emit(hit_block)
            event.accept()
            return
            
        # Clicked on blank space! Find track index and clicked time
        self.right_clicked_time = int((mx + self.scroll_offset_x) * self.scale)
        # Snap clicked time to 10ms grid
        self.right_clicked_time = round(self.right_clicked_time / 10.0) * 10
        self.right_clicked_track = max(0, min(self.num_tracks - 1, int((my - self.ruler_height) / self.track_height)))
        
        # Add the block depending on the track
        if 0 <= self.right_clicked_track <= 3:
            self.insert_keyboard_action()
        elif self.right_clicked_track == 4:
            self.insert_mouse_click_action()
        elif self.right_clicked_track == 5:
            self.insert_mouse_move_action()
            
        event.accept()

    def mousePressEvent(self, event):
        # Middle Mouse Button -> Panning
        if event.button() == Qt.MouseButton.MiddleButton:
            mx, my = event.position().x(), event.position().y()
            self.canvas_panning = True
            self.pan_start_x = mx
            self.pan_start_offset = self.scroll_offset_x
            event.accept()
            return
            
        if event.button() != Qt.MouseButton.LeftButton:
            return
            
        mx, my = event.position().x(), event.position().y()
        h = self.height()
        w = self.width()
        
        # 1. Clicked on the bottom scrollbar area -> scroll drag starts
        w_total = max(float(w), self.timeline_max_ms / self.scale)
        if w_total > w and my >= h - 14:
            thumb_w = max(40.0, (w / w_total) * w)
            thumb_x = (self.scroll_offset_x / (w_total - w)) * (w - thumb_w)
            
            # Check if clicked on thumb
            if thumb_x <= mx <= thumb_x + thumb_w:
                self.scrolling = True
                self.scroll_drag_start_x = mx
                self.scroll_drag_start_offset = self.scroll_offset_x
            event.accept()
            return
            
        # 2. Clicked on ruler area -> Seek Playhead
        if my <= self.ruler_height:
            new_playhead = int((mx + self.scroll_offset_x) * self.scale)
            self.set_playhead(new_playhead)
            self.blocksChanged.emit() # Notify parent OSD & trigger state updates
            event.accept()
            return
            
        # 3. Clicked on tracks -> Test Block hit
        hit_block, part = self._hit_test(event.position())
        if hit_block:
            self.push_undo_state()
            if hit_block not in self.selected_blocks:
                self.selected_block = hit_block
                self.selected_blocks = [hit_block]
                self.selectionChanged.emit(hit_block)
                self.multiSelectionChanged.emit(self.selected_blocks)
            else:
                self.selected_block = hit_block
                
            self.dragged_block = hit_block
            self.drag_mode = part
            self.drag_start_x = mx
            self.drag_start_time = hit_block.start_time
            self.drag_start_duration = hit_block.duration
            self.drag_start_end_time = hit_block.start_time + hit_block.duration
            self.drag_start_positions = {b.block_id: b.start_time for b in self.selected_blocks}
            self.update()
        else:
            self.selected_block = None
            self.selected_blocks = []
            self.selectionChanged.emit(None)
            self.multiSelectionChanged.emit([])
            
            # If Ctrl key is held, trigger panning. Otherwise start marquee selection.
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.canvas_panning = True
                self.pan_start_x = mx
                self.pan_start_offset = self.scroll_offset_x
            else:
                self.marquee_selection_active = True
                self.marquee_start_pos = event.position()
                self.marquee_current_pos = event.position()
            self.update()
            
        event.accept()

    def mouseMoveEvent(self, event):
        self._handle_mouse_move(event.position().x(), event.position().y())
        event.accept()

    def _handle_mouse_move(self, mx, my):
        w = self.width()
        h = self.height()
        w_total = max(float(w), self.timeline_max_ms / self.scale)
        
        # Handle scrollbar thumb dragging
        if self.scrolling:
            dx = mx - self.scroll_drag_start_x
            thumb_w = max(40.0, (w / w_total) * w)
            if w > thumb_w:
                d_offset = dx * (w_total - w) / (w - thumb_w)
                self.set_scroll_offset(max(0.0, min(w_total - w, self.scroll_drag_start_offset + d_offset)))
            return
            
        # Handle canvas panning
        if getattr(self, "canvas_panning", False):
            dx = mx - self.pan_start_x
            self.set_scroll_offset(max(0.0, min(w_total - w, self.pan_start_offset - dx)))
            return
            
        # Handle marquee selection dragging
        if getattr(self, "marquee_selection_active", False):
            self.marquee_current_pos = QPoint(int(mx), int(my))
            x1 = min(self.marquee_start_pos.x(), self.marquee_current_pos.x())
            y1 = min(self.marquee_start_pos.y(), self.marquee_current_pos.y())
            x2 = max(self.marquee_start_pos.x(), self.marquee_current_pos.x())
            y2 = max(self.marquee_start_pos.y(), self.marquee_current_pos.y())
            sel_rect = QRectF(x1, y1, x2 - x1, y2 - y1)
            
            selected = []
            for b in self.blocks:
                br = self._get_block_rect(b)
                if sel_rect.intersects(br):
                    selected.append(b)
            self.selected_blocks = selected
            if len(selected) == 1:
                self.selected_block = selected[0]
                self.selectionChanged.emit(selected[0])
            else:
                self.selected_block = None
                self.selectionChanged.emit(None)
                
            self.multiSelectionChanged.emit(self.selected_blocks)
            self.update()
            
            # Check edge scrolling
            if mx < 30 and self.scroll_offset_x > 0:
                self.auto_scroll_dir = -1
                if not self.auto_scroll_timer.isActive():
                    self.auto_scroll_timer.start(30)
            elif mx > w - 30 and self.scroll_offset_x < w_total - w:
                self.auto_scroll_dir = 1
                if not self.auto_scroll_timer.isActive():
                    self.auto_scroll_timer.start(30)
            else:
                self.auto_scroll_timer.stop()
            return
            
        # Handle block dragging/resizing
        if self.dragged_block and self.drag_mode:
            dx = mx - self.drag_start_x
            dt = int(dx * self.scale)
            
            if self.drag_mode == "body":
                if len(self.selected_blocks) > 1:
                    # Move all selected blocks horizontally together as a group
                    min_dt = -9999999
                    max_dt = 9999999
                    for b in self.selected_blocks:
                        orig_start = self.drag_start_positions.get(b.block_id, b.start_time)
                        left_lim, right_lim = self._get_track_drag_limits(b, b.track_index, exclude_selected=True)
                        b_min_dt = left_lim - orig_start
                        b_max_dt = right_lim - orig_start - b.duration
                        min_dt = max(min_dt, b_min_dt)
                        max_dt = min(max_dt, b_max_dt)
                    
                    clamped_dt = max(min_dt, min(max_dt, dt))
                    clamped_dt = round(clamped_dt / 10.0) * 10
                    clamped_dt = max(min_dt, min(max_dt, clamped_dt))
                    
                    for b in self.selected_blocks:
                        orig_start = self.drag_start_positions.get(b.block_id, b.start_time)
                        b.start_time = orig_start + clamped_dt
                else:
                    # 1. Determine target track index based on mouse Y position
                    target_track = int((my - self.ruler_height) / self.track_height)
                    if self.dragged_block.type_str == "keyboard":
                        target_track = max(0, min(3, target_track))
                    elif self.dragged_block.type_str == "mouse_click":
                        target_track = 4
                    else:
                        target_track = 5
                        
                    # 2. Get limits for this target track
                    left_lim, right_lim = self._get_track_drag_limits(self.dragged_block, target_track)
                    
                    # 3. Calculate new horizontal start time
                    new_start = max(0, self.drag_start_time + dt)
                    new_start = self._apply_snapping(self.dragged_block, new_start, self.dragged_block.duration, drag_type="body")
                    new_start = round(new_start / 10.0) * 10
                    
                    # 4. Clamp start time to prevent overlap on this track
                    if new_start < left_lim:
                        new_start = left_lim
                    if new_start + self.dragged_block.duration > right_lim:
                        new_start = right_lim - self.dragged_block.duration
                        
                    # Only update track index if it fits on the target track
                    if new_start >= left_lim and new_start + self.dragged_block.duration <= right_lim:
                        self.dragged_block.track_index = target_track
                        self.dragged_block.start_time = new_start
                    
            elif self.drag_mode == "right_edge":
                left_lim, right_lim = self._get_track_drag_limits(self.dragged_block, self.dragged_block.track_index)
                new_dur = max(10, self.drag_start_duration + dt)
                new_dur = self._apply_snapping(self.dragged_block, self.dragged_block.start_time, new_dur, drag_type="right_edge")
                new_dur = max(10, round(new_dur / 10.0) * 10)
                
                # Clamp duration to prevent overlapping the right block
                if self.dragged_block.start_time + new_dur > right_lim:
                    new_dur = right_lim - self.dragged_block.start_time
                self.dragged_block.duration = new_dur
                
            elif self.drag_mode == "left_edge":
                left_lim, right_lim = self._get_track_drag_limits(self.dragged_block, self.dragged_block.track_index)
                new_start = max(0, self.drag_start_time + dt)
                if new_start >= self.drag_start_end_time - 10:
                    new_start = self.drag_start_end_time - 10
                new_start = self._apply_snapping(self.dragged_block, new_start, self.drag_start_end_time - new_start, drag_type="left_edge")
                new_start = round(new_start / 10.0) * 10
                
                # Clamp start time to prevent overlapping the left block
                if new_start < left_lim:
                    new_start = left_lim
                    
                self.dragged_block.start_time = new_start
                self.dragged_block.duration = self.drag_start_end_time - new_start
                
            self.update()
            
            # Check edge scrolling
            if mx < 30 and self.scroll_offset_x > 0:
                self.auto_scroll_dir = -1
                if not self.auto_scroll_timer.isActive():
                    self.auto_scroll_timer.start(30)
            elif mx > w - 30 and self.scroll_offset_x < w_total - w:
                self.auto_scroll_dir = 1
                if not self.auto_scroll_timer.isActive():
                    self.auto_scroll_timer.start(30)
            else:
                self.auto_scroll_timer.stop()
            return

        # Otherwise hover styling cursors
        if my >= h - 14 and w_total > w:
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return
            
        block, part = self._hit_test(QPoint(int(mx), int(my)))
        if block:
            if part in ("left_edge", "right_edge"):
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            else:
                self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def on_auto_scroll(self):
        w = self.width()
        w_total = max(float(w), self.timeline_max_ms / self.scale)
        scroll_speed = 20.0
        
        if self.auto_scroll_dir < 0 and self.scroll_offset_x <= 0:
            return
        if self.auto_scroll_dir > 0 and self.scroll_offset_x >= w_total - w:
            return
            
        new_offset = self.scroll_offset_x + self.auto_scroll_dir * scroll_speed
        self.set_scroll_offset(max(0.0, min(w_total - w, new_offset)))
        
        if self.dragged_block and self.drag_mode:
            self.drag_start_x -= self.auto_scroll_dir * scroll_speed
        elif getattr(self, "marquee_selection_active", False):
            self.marquee_start_pos.setX(self.marquee_start_pos.x() - self.auto_scroll_dir * scroll_speed)
            
        pos = self.mapFromGlobal(self.cursor().pos())
        self._handle_mouse_move(pos.x(), pos.y())

    def mouseReleaseEvent(self, event):
        self.auto_scroll_timer.stop()
        if self.scrolling:
            self.scrolling = False
            self.update()
            event.accept()
            return
            
        if getattr(self, "canvas_panning", False):
            self.canvas_panning = False
            self.update()
            event.accept()
            return
            
        if getattr(self, "marquee_selection_active", False):
            self.marquee_selection_active = False
            self.multiSelectionChanged.emit(self.selected_blocks)
            self.update()
            event.accept()
            return
            
        if self.dragged_block:
            # Check if any actual time or duration changed
            any_changed = False
            if self.dragged_block.start_time != self.drag_start_time or self.dragged_block.duration != self.drag_start_duration:
                any_changed = True
            else:
                for b in self.selected_blocks:
                    if b.block_id in self.drag_start_positions and b.start_time != self.drag_start_positions[b.block_id]:
                        any_changed = True
                        break
                        
            if not any_changed:
                if self.undo_stack:
                    self.undo_stack.pop()
            
            # Re-sort blocks list chronologically after movement
            self.blocks.sort(key=lambda b: b.start_time)
            
            # Recalculate timeline bounds
            max_time = 5000
            for b in self.blocks:
                max_time = max(max_time, b.start_time + b.duration)
            self.timeline_max_ms = max_time + 2000
            
            self.dragged_block = None
            self.drag_mode = None
            self.snap_line_ms = None
            
            # Notify changes to save in profile
            if any_changed:
                self.blocksChanged.emit()
            self.update()
            
        event.accept()

    def wheelEvent(self, event):
        """Scrolls timeline horizontally using mouse wheel, zooms with Ctrl+Wheel."""
        mx = event.position().x()
        w = self.width()
        w_total = max(float(w), self.timeline_max_ms / self.scale)
        
        # 1. Zoom with Ctrl + Scroll Wheel (Centered at mouse pointer)
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            angle = event.angleDelta().y()
            t_under_mouse = (mx + self.scroll_offset_x) * self.scale
            
            if angle > 0:
                new_scale = max(1.0, self.scale / 1.15)
            else:
                new_scale = min(100.0, self.scale * 1.15)
                
            # Align new scroll offset so target time remains under mouse pointer
            new_w_total = max(float(w), self.timeline_max_ms / new_scale)
            new_offset = (t_under_mouse / new_scale) - mx
            self.set_scale(new_scale)
            self.set_scroll_offset(max(0.0, min(new_w_total - w, new_offset)))
            self.blocksChanged.emit()
        else:
            # 2. Scroll horizontally with wheel
            angle = event.angleDelta().y()
            # Scroll offset speed proportional to scale
            self.set_scroll_offset(max(0.0, min(w_total - w, self.scroll_offset_x - angle * 0.5)))
            
        event.accept()

    def keyPressEvent(self, event):
        # Allow Delete or Backspace key to delete the selected block
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and (self.selected_block or self.selected_blocks):
            self.delete_selected_block()
            event.accept()
        elif event.modifiers() & Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_C:
            self.copy_selected_block()
            event.accept()
        elif event.modifiers() & Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_V:
            self.right_clicked_time = self.playhead_ms
            self.paste_block()
            event.accept()
        elif event.modifiers() & Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_X:
            self.cut_selected_block()
            event.accept()
        elif event.modifiers() & Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_D:
            self.duplicate_selected_block()
            event.accept()
        elif event.modifiers() & Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_Z:
            self.undo()
            event.accept()
        else:
            super().keyPressEvent(event)

    # --- Right-Click Context Menu Event ---
    def contextMenuEvent(self, event):
        mx, my = event.pos().x(), event.pos().y()
        h = self.height()
        
        # Prevent context menus overlapping scrollbar click
        if my >= h - 14:
            return
            
        # Hit test at clicked position
        hit_block, part = self._hit_test(event.pos())
        if hit_block:
            if hit_block not in self.selected_blocks:
                self.selected_block = hit_block
                self.selected_blocks = [hit_block]
                self.selectionChanged.emit(hit_block)
                self.multiSelectionChanged.emit(self.selected_blocks)
            else:
                self.selected_block = hit_block
            self.update()
            
        self.right_clicked_time = int((mx + self.scroll_offset_x) * self.scale)
        # 10ms grid snapping
        self.right_clicked_time = round(self.right_clicked_time / 10.0) * 10
        self.right_clicked_track = max(0, min(self.num_tracks - 1, int((my - self.ruler_height) / self.track_height)))
        
        menu = QMenu(self)
        
        has_sel = (self.selected_block is not None) or bool(self.selected_blocks)
        
        act_cut = QAction("剪切 (Cut) \tCtrl+X", self)
        act_cut.setEnabled(has_sel)
        act_cut.triggered.connect(self.cut_selected_block)
        
        act_copy = QAction("复制 (Copy) \tCtrl+C", self)
        act_copy.setEnabled(has_sel)
        act_copy.triggered.connect(self.copy_selected_block)
        
        act_paste = QAction("粘贴 (Paste) \tCtrl+V", self)
        act_paste.setEnabled(hasattr(self, "clipboard_blocks") and bool(self.clipboard_blocks))
        act_paste.triggered.connect(self.paste_block)
        
        act_dup = QAction("克隆 (Duplicate) \tCtrl+D", self)
        act_dup.setEnabled(has_sel)
        act_dup.triggered.connect(self.duplicate_selected_block)
        
        act_del = QAction("删除 (Delete) \tDel", self)
        act_del.setEnabled(has_sel)
        act_del.triggered.connect(self.delete_selected_block)
        
        menu.addAction(act_cut)
        menu.addAction(act_copy)
        menu.addAction(act_paste)
        menu.addAction(act_dup)
        menu.addAction(act_del)
        
        menu.addSeparator()
        
        if 0 <= self.right_clicked_track <= 3:
            act_ins_kb = QAction("插入键盘按键 (Insert Keyboard Key)", self)
            act_ins_kb.triggered.connect(self.insert_keyboard_action)
            menu.addAction(act_ins_kb)
        elif self.right_clicked_track == 4:
            act_ins_click = QAction("插入鼠标点击 (Insert Mouse Click)", self)
            act_ins_click.triggered.connect(self.insert_mouse_click_action)
            menu.addAction(act_ins_click)
        elif self.right_clicked_track == 5:
            act_ins_move = QAction("插入鼠标移动段 (Insert Mouse Move)", self)
            act_ins_move.triggered.connect(self.insert_mouse_move_action)
            menu.addAction(act_ins_move)
        
        menu.exec(event.globalPos())

    def copy_selected_block(self):
        self.clipboard_blocks = []
        blocks_to_copy = self.selected_blocks if self.selected_blocks else ([self.selected_block] if self.selected_block else [])
        if not blocks_to_copy:
            return
            
        min_start = min(b.start_time for b in blocks_to_copy)
        for b in blocks_to_copy:
            self.clipboard_blocks.append({
                "type_str": b.type_str,
                "duration": b.duration,
                "key_code": b.key_code,
                "key_name": b.key_name,
                "x": b.x,
                "y": b.y,
                "path_points": list(b.path_points),
                "track_index": b.track_index,
                "rel_offset": b.start_time - min_start
            })

    def cut_selected_block(self):
        self.copy_selected_block()
        self.delete_selected_block()

    def paste_block(self):
        if hasattr(self, "clipboard_blocks") and self.clipboard_blocks:
            self.push_undo_state()
            new_blocks = []
            base_time = self.right_clicked_time
            
            for item in self.clipboard_blocks:
                bid = f"paste_{int(time.time())}_{random.randint(100, 999)}"
                target_start = base_time + item["rel_offset"]
                new_start, track_idx = self._find_non_overlapping_position(
                    item["type_str"],
                    target_start,
                    item["duration"],
                    item["track_index"]
                )
                new_block = UIBlockItem(
                    block_id=bid,
                    start_time=new_start,
                    duration=item["duration"],
                    type_str=item["type_str"],
                    key_code=item["key_code"],
                    key_name=item["key_name"],
                    x=item["x"],
                    y=item["y"],
                    path_points=list(item["path_points"]),
                    track_index=track_idx
                )
                new_blocks.append(new_block)
                self.blocks.append(new_block)
                
            self.blocks.sort(key=lambda b: b.start_time)
            self.selected_blocks = new_blocks
            if len(new_blocks) == 1:
                self.selected_block = new_blocks[0]
                self.selectionChanged.emit(new_blocks[0])
            else:
                self.selected_block = None
                self.selectionChanged.emit(None)
            self.multiSelectionChanged.emit(self.selected_blocks)
            self.blocksChanged.emit()
            self.update()

    def duplicate_selected_block(self):
        blocks_to_dup = self.selected_blocks if self.selected_blocks else ([self.selected_block] if self.selected_block else [])
        if not blocks_to_dup:
            return
        self.push_undo_state()
            
        min_start = min(b.start_time for b in blocks_to_dup)
        max_end = max(b.start_time + b.duration for b in blocks_to_dup)
        shift_amount = (max_end - min_start) + 10
        
        new_blocks = []
        for b in blocks_to_dup:
            bid = f"dup_{int(time.time())}_{random.randint(100, 999)}"
            target_start = b.start_time + shift_amount
            new_start, track_idx = self._find_non_overlapping_position(
                b.type_str,
                target_start,
                b.duration,
                b.track_index
            )
            new_block = UIBlockItem(
                block_id=bid,
                start_time=new_start,
                duration=b.duration,
                type_str=b.type_str,
                key_code=b.key_code,
                key_name=b.key_name,
                x=b.x,
                y=b.y,
                path_points=list(b.path_points),
                track_index=track_idx
            )
            new_blocks.append(new_block)
            self.blocks.append(new_block)
            
        self.blocks.sort(key=lambda b: b.start_time)
        self.selected_blocks = new_blocks
        if len(new_blocks) == 1:
            self.selected_block = new_blocks[0]
            self.selectionChanged.emit(new_blocks[0])
        else:
            self.selected_block = None
            self.selectionChanged.emit(None)
        self.multiSelectionChanged.emit(self.selected_blocks)
        self.blocksChanged.emit()
        self.update()

    def delete_selected_block(self):
        self.push_undo_state()
        if self.selected_blocks:
            for b in self.selected_blocks:
                if b in self.blocks:
                    self.blocks.remove(b)
            self.selected_block = None
            self.selected_blocks = []
            self.selectionChanged.emit(None)
            self.multiSelectionChanged.emit([])
            self.blocksChanged.emit()
            self.update()
        elif self.selected_block:
            if self.selected_block in self.blocks:
                self.blocks.remove(self.selected_block)
            self.selected_block = None
            self.selectionChanged.emit(None)
            self.blocksChanged.emit()
            self.update()

    def insert_keyboard_action(self):
        self.push_undo_state()
        bid = f"kb_ins_{int(time.time())}_{random.randint(100, 999)}"
        new_start, track_idx = self._find_non_overlapping_position(
            "keyboard",
            self.right_clicked_time,
            100,
            self.right_clicked_track
        )
        new_block = UIBlockItem(
            block_id=bid,
            start_time=new_start,
            duration=100,
            type_str="keyboard",
            key_code=0x41, # Key 'A'
            key_name="A",
            track_index=track_idx
        )
        self.blocks.append(new_block)
        self.blocks.sort(key=lambda b: b.start_time)
        self.selected_block = new_block
        self.selectionChanged.emit(new_block)
        self.blocksChanged.emit()
        self.update()

    def insert_mouse_click_action(self):
        self.push_undo_state()
        bid = f"ms_click_ins_{int(time.time())}_{random.randint(100, 999)}"
        new_start, track_idx = self._find_non_overlapping_position(
            "mouse_click",
            self.right_clicked_time,
            100,
            self.right_clicked_track
        )
        new_block = UIBlockItem(
            block_id=bid,
            start_time=new_start,
            duration=100,
            type_str="mouse_click",
            key_code=0x01, # Left click
            key_name="Left Click",
            x=500,
            y=500,
            track_index=track_idx
        )
        self.blocks.append(new_block)
        self.blocks.sort(key=lambda b: b.start_time)
        self.selected_block = new_block
        self.selectionChanged.emit(new_block)
        self.blocksChanged.emit()
        self.update()

    def insert_mouse_move_action(self):
        self.push_undo_state()
        bid = f"ms_move_ins_{int(time.time())}_{random.randint(100, 999)}"
        new_start, track_idx = self._find_non_overlapping_position(
            "mouse_move",
            self.right_clicked_time,
            100,
            self.right_clicked_track
        )
        pts = [(0, 500, 500), (100, 510, 510)]
        new_block = UIBlockItem(
            block_id=bid,
            start_time=new_start,
            duration=100,
            type_str="mouse_move",
            x=510,
            y=510,
            path_points=pts,
            track_index=track_idx
        )
        self.blocks.append(new_block)
        self.blocks.sort(key=lambda b: b.start_time)
        self.selected_block = new_block
        self.selectionChanged.emit(new_block)
        self.blocksChanged.emit()
        self.update()

    # --- Drawing QPainter Canvas ---
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        w = self.width()
        h = self.height()
        max_time = self.timeline_max_ms
        
        # Clamp scroll offset based on cached bounds
        w_total = max(float(w), self.timeline_max_ms / self.scale)
        self.scroll_offset_x = max(0.0, min(w_total - w, self.scroll_offset_x))
        
        # 1. Fill background panel
        painter.fillRect(0, 0, w, h, QColor(25, 25, 25))
        
        # 2. Draw tracks lanes backgrounds and dividers
        for i in range(self.num_tracks):
            ty = self.ruler_height + i * self.track_height
            if i < 4:
                bg_color = QColor(30, 30, 30) if i % 2 == 0 else QColor(35, 35, 35)
                lbl_color = QColor(255, 140, 0, 100) # Semi-transparent orange
                lbl_text = f"KB {i + 1}"
            elif i == 4:
                bg_color = QColor(25, 30, 35)
                lbl_color = QColor(0, 191, 255, 100) # Semi-transparent cyan
                lbl_text = "MS Click"
            else:
                bg_color = QColor(30, 35, 40)
                lbl_color = QColor(186, 85, 211, 100) # Semi-transparent purple
                lbl_text = "MS Move"
                
            painter.fillRect(0, ty, w, self.track_height, bg_color)
            
            # Draw track name indicator text label on left side
            painter.setPen(QPen(lbl_color, 1))
            painter.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
            painter.drawText(6, ty + 20, lbl_text)
            
            # Horizontal divider line between tracks
            painter.setPen(QPen(QColor(55, 55, 55), 1))
            painter.drawLine(0, ty + self.track_height, w, ty + self.track_height)
        
        # 3. Calculate dynamic grid ticks based on scale
        intervals = [10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 30000, 60000]
        major_intv = 1000
        for intv in intervals:
            if intv / self.scale >= 70:
                major_intv = intv
                break
                
        minor_intv = major_intv / 5.0
        
        # Draw grid background vertical lines
        painter.setPen(QPen(QColor(255, 255, 255, 12), 1, Qt.PenStyle.SolidLine))
        t_grid = max(0.0, float(int((self.scroll_offset_x * self.scale) / major_intv) * major_intv))
        while t_grid <= max_time:
            gx = int(t_grid / self.scale) - self.scroll_offset_x
            if gx > w:
                break
            if gx >= 0:
                painter.drawLine(gx, self.ruler_height, gx, h)
            t_grid += major_intv

        # 4. Draw Ruler background, ticks, and text labels
        painter.fillRect(0, 0, w, self.ruler_height, QColor(18, 18, 18))
        painter.setPen(QPen(QColor(150, 150, 150), 1))
        painter.drawLine(0, self.ruler_height, w, self.ruler_height)
        
        t_tick = max(0.0, float(int((self.scroll_offset_x * self.scale) / minor_intv) * minor_intv))
        while t_tick <= max_time:
            tx = int(t_tick / self.scale) - self.scroll_offset_x
            if tx > w:
                break
                
            if tx >= 0:
                # Major tick
                if abs(t_tick % major_intv) < 0.001:
                    if major_intv >= 1000 and (int(t_tick) % 1000) == 0:
                        lbl = f"{int(t_tick) // 1000}s"
                    elif major_intv >= 1000:
                        lbl = f"{t_tick / 1000.0:.1f}s"
                    else:
                        lbl = f"{int(t_tick)}ms"
                        
                    painter.setPen(QPen(QColor(150, 150, 150), 1))
                    painter.setFont(QFont("Segoe UI", 8))
                    painter.drawLine(tx, self.ruler_height - 6, tx, self.ruler_height)
                    painter.drawText(tx + 4, self.ruler_height - 6, lbl)
                else:
                    # Minor tick
                    painter.setPen(QPen(QColor(80, 80, 80), 1))
                    painter.drawLine(tx, self.ruler_height - 3, tx, self.ruler_height)
                
            t_tick += minor_intv
            
        # 5. Draw Block Items
        for b in self.blocks:
            rect = self._get_block_rect(b)
            
            # Setup colors based on action type
            if b.type_str == "keyboard":
                base_color = QColor(255, 140, 0) # Fluent Orange
            elif b.type_str == "mouse_click":
                base_color = QColor(0, 191, 255) # Cyan
            else:
                base_color = QColor(186, 85, 211) # Purple for path moves
                
            # If selected, highlight with bright borders
            is_selected = (self.selected_block and self.selected_block.block_id == b.block_id) or (b in self.selected_blocks)
            
            if is_selected:
                painter.setBrush(QBrush(QColor(base_color.red(), base_color.green(), base_color.blue(), 180)))
                painter.setPen(QPen(QColor(255, 255, 255, 255), 2))
            else:
                painter.setBrush(QBrush(QColor(base_color.red(), base_color.green(), base_color.blue(), 100)))
                painter.setPen(QPen(QColor(base_color.red(), base_color.green(), base_color.blue(), 180), 1))
                
            # Draw sharp rectangle box (no rounded corners)
            painter.drawRect(rect)
            
            # Draw text inside block
            painter.setPen(QPen(QColor(255, 255, 255, 240)))
            painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            
            if b.type_str == "keyboard":
                lbl = f"{b.key_name} ({b.duration}ms)"
            elif b.type_str == "mouse_click":
                lbl = f"点击:{b.key_name} ({b.duration}ms)"
            else:
                lbl = f"移动 ({b.duration}ms)"
                
            # Clip string if rect width is too small
            metrics = painter.fontMetrics()
            elided_lbl = metrics.elidedText(lbl, Qt.TextElideMode.ElideRight, max(5, int(rect.width() - 8)))
            
            if rect.width() > 15:
                painter.drawText(rect.adjusted(6, 0, -6, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided_lbl)
            


        # Draw Snapping Guide Line with fade-out animation
        if hasattr(self, "snap_line_ms") and self.snap_line_ms is not None and hasattr(self, "snap_opacity") and self.snap_opacity > 0:
            spx = int(self.snap_line_ms / self.scale) - self.scroll_offset_x
            if 0 <= spx <= w:
                painter.setPen(QPen(QColor(255, 215, 0, int(self.snap_opacity)), 2, Qt.PenStyle.DashLine)) # Gold/Yellow Dash Line
                painter.drawLine(spx, self.ruler_height, spx, h)

        # 6. Draw Red Playhead
        px = int(self.playhead_ms / self.scale) - self.scroll_offset_x
        if 0 <= px <= w:
            # Draw playhead line
            painter.setPen(QPen(QColor(255, 23, 68), 1.5))
            painter.drawLine(px, 0, px, h)
            
            # Draw playhead top handles (Triangle)
            painter.setBrush(QBrush(QColor(255, 23, 68)))
            painter.setPen(Qt.PenStyle.NoPen)
            
            from PySide6.QtGui import QPolygonF
            poly = QPolygonF()
            poly.append(QPoint(px - 6, 0))
            poly.append(QPoint(px + 6, 0))
            poly.append(QPoint(px, 8))
            painter.drawPolygon(poly)
            
        # 7. Draw bottom Scrollbar
        if w_total > w:
            sb_y = h - 10
            sb_h = 6
            painter.fillRect(0, sb_y, w, sb_h, QColor(45, 45, 45, 120))
            
            thumb_w = max(40.0, (w / w_total) * w)
            thumb_x = (self.scroll_offset_x / (w_total - w)) * (w - thumb_w)
            
            thumb_color = QColor(100, 100, 100, 180)
            if self.scrolling:
                thumb_color = QColor(140, 140, 140, 220)
                
            painter.setBrush(QBrush(thumb_color))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRect(QRectF(thumb_x, sb_y, thumb_w, sb_h))
            
        # 8. Draw Marquee Selection Rectangle
        if getattr(self, "marquee_selection_active", False):
            x1 = min(self.marquee_start_pos.x(), self.marquee_current_pos.x())
            y1 = min(self.marquee_start_pos.y(), self.marquee_current_pos.y())
            x2 = max(self.marquee_start_pos.x(), self.marquee_current_pos.x())
            y2 = max(self.marquee_start_pos.y(), self.marquee_current_pos.y())
            
            painter.setBrush(QBrush(QColor(0, 191, 255, 30))) # Transparent Cyan
            painter.setPen(QPen(QColor(0, 191, 255, 180), 1, Qt.PenStyle.DashLine))
            painter.drawRect(QRectF(x1, y1, x2 - x1, y2 - y1))
