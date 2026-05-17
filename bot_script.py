import argparse
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple
import re

# 將專案根目錄加入 path，以便導入 barricade_core
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from playwright.sync_api import Page, sync_playwright
from sb3_contrib import MaskablePPO

from barricade_core import QuoridorEnv, Board, action_id_to_action, pos_to_xy

BOARD_SIZE = 9


def parse_page_state(page: Page) -> Dict:
    """從 barricade.gg/local 頁面讀取棋盤狀態（使用 Playwright Python API）。"""
    board = page.query_selector('div[data-tutorial="board"]')
    if not board:
        return None

    cells = []
    for el in board.query_selector_all('div'):
        style = el.get_attribute('style') or ''
        m = re.search(r'grid-area:\s*([^;]+)', style)
        if not m:
            continue
        parts = [p.strip() for p in m.group(1).split('/')]
        if len(parts) != 2:
            continue
        try:
            row = int(parts[0])
            col = int(parts[1])
        except ValueError:
            continue
        if row < 2 or row > 18 or col < 2 or col > 18 or row % 2 != 0 or col % 2 != 0:
            continue
        box = el.bounding_box()
        if not box:
            continue
        cells.append({
            'row': (row - 2) // 2,
            'col': (col - 2) // 2,
            'grid_raw': m.group(1),
            'cx': box['x'] + box['width'] / 2,
            'cy': box['y'] + box['height'] / 2,
        })

    pawns = []
    for el in page.query_selector_all('div.aspect-square.bg-red-500, div.aspect-square.bg-blue-500'):
        box = el.bounding_box()
        if not box:
            continue
        cls = el.get_attribute('class') or ''
        color = 'red' if 'bg-red-500' in cls else 'blue'
        pawns.append({'color': color, 'cx': box['x'] + box['width'] / 2, 'cy': box['y'] + box['height'] / 2})

    # 使用聚類方法將解析到的 cell cx/cy 分組為 9 個欄與 9 個列 (容忍重複元素)
    # 先取出所有 cx, cy
    cx_list = sorted({c['cx'] for c in cells})
    cy_list = sorted({c['cy'] for c in cells})

    def cluster_centers(vals, tol=20):
        clusters = []
        for v in vals:
            if not clusters or abs(v - clusters[-1][-1]) > tol:
                clusters.append([v])
            else:
                clusters[-1].append(v)
        return [sum(g) / len(g) for g in clusters]

    col_centers = cluster_centers(cx_list, tol=20)
    row_centers = cluster_centers(cy_list, tol=20)

    # 重新標記每個 cell 的 row/col
    for c in cells:
        # find nearest cluster index
        col_idx = min(range(len(col_centers)), key=lambda i: abs(c['cx'] - col_centers[i]))
        row_idx = min(range(len(row_centers)), key=lambda i: abs(c['cy'] - row_centers[i]))
        c['row'] = int(row_idx)
        c['col'] = int(col_idx)

    walls = {'horizontal': [], 'vertical': []}
    for el in page.query_selector_all('[data-testid^="slot-horizontal-"]'):
        testid = el.get_attribute('data-testid') or ''
        parts = testid.split('-')
        if len(parts) < 4:
            continue
        try:
            row = int(parts[2]); col = int(parts[3])
        except ValueError:
            continue
        occupied = page.evaluate('(e) => !!e.firstElementChild', el)
        walls['horizontal'].append({'row': row, 'col': col, 'occupied': occupied})

    for el in page.query_selector_all('[data-testid^="slot-vertical-"]'):
        testid = el.get_attribute('data-testid') or ''
        parts = testid.split('-')
        if len(parts) < 4:
            continue
        try:
            row = int(parts[2]); col = int(parts[3])
        except ValueError:
            continue
        occupied = page.evaluate('(e) => !!e.firstElementChild', el)
        walls['vertical'].append({'row': row, 'col': col, 'occupied': occupied})

    players = []
    for el in page.query_selector_all('div[data-tutorial="player-cards"]'):
        text = el.inner_text().strip()
        lines = [l for l in (text.split('\n')) if l.strip()]
        name = lines[0] if lines else ''
        wallsLeft = 0
        if len(lines) > 1:
            try:
                m = re.search(r"(\d+)\s*/\s*(\d+)", lines[1])
                if m:
                    wallsLeft = int(m.group(1))
                else:
                    m2 = re.search(r"(\d+)", lines[1])
                    wallsLeft = int(m2.group(1)) if m2 else 0
            except Exception:
                wallsLeft = 0
        cls = el.get_attribute('class') or ''
        active = 'border-l-green-500' in cls or 'bg-green-500/20' in cls
        # detect pawn color inside the card (red/blue)
        color = None
        try:
            if el.query_selector('.bg-red-500'):
                color = 'red'
            elif el.query_selector('.bg-blue-500'):
                color = 'blue'
        except Exception:
            color = None
        players.append({'name': name, 'wallsLeft': wallsLeft, 'active': active, 'color': color, 'text': text})

    current = next((p for p in players if p.get('active')), None)
    current_color = current.get('color') if current and current.get('color') else ('red' if current else 'red')
    return {'cells': cells, 'pawns': pawns, 'walls': walls, 'players': players, 'currentPlayer': current_color}


def find_pawn_positions(state: Dict) -> Tuple[Tuple[int, int], Tuple[int, int]]:
    cell_list = state['cells']
    if len(cell_list) < BOARD_SIZE * BOARD_SIZE:
        raise RuntimeError('未能解析完整棋盤格，解析到的格子數不足 (需至少 81 個)')

    red_pos = None
    blue_pos = None

    remaining = list(cell_list)
    for pawn in state['pawns']:
        if not remaining:
            break
        best = min(
            remaining,
            key=lambda cell: (cell['cx'] - pawn['cx']) ** 2 + (cell['cy'] - pawn['cy']) ** 2,
        )
        try:
            print(f"匹配 pawn {pawn['color']} at ({pawn['cx']:.2f},{pawn['cy']:.2f}) -> best={best}")
        except Exception:
            pass
        remaining.remove(best)
        board_x = int(best['col'])
        board_y = BOARD_SIZE - 1 - int(best['row'])

        if pawn['color'] == 'red':
            red_pos = (board_x, board_y)
        else:
            blue_pos = (board_x, board_y)

    if red_pos is None or blue_pos is None:
        raise RuntimeError('無法解析雙方棋子位置')

    return red_pos, blue_pos


def sync_env_from_page(page: Page, env: QuoridorEnv) -> Tuple[List[int], List[bool], Dict]:
    try:
        state = parse_page_state(page)
    except Exception as e:
        try:
            Path(ROOT_DIR / 'logs').mkdir(parents=True, exist_ok=True)
            html = page.content()
            with open(Path(ROOT_DIR / 'logs') / 'last_page.html', 'w', encoding='utf-8') as f:
                f.write(html)
            print(f'parse_page_state 失敗，已將頁面 HTML 儲存到 logs/last_page.html: {e}')
        except Exception as inner_e:
            print(f'在儲存錯誤頁面時發生錯誤: {inner_e}')
        raise
    if state is None:
        raise RuntimeError('無法讀取 barricade.gg/local 的棋盤')

    # debug: 印出解析到的頁面元素數量
    try:
        print(f"解析頁面: cells={len(state.get('cells', []))}, pawns={len(state.get('pawns', []))}, h_walls={len(state.get('walls', {}).get('horizontal', []))}, v_walls={len(state.get('walls', {}).get('vertical', []))}, players={len(state.get('players', []))}, current={state.get('currentPlayer')}")
        print('pawns:', state.get('pawns'))
        print('players:', state.get('players'))
        print('cells sample:', state.get('cells')[:10])
    except Exception:
        pass

    red_pos, blue_pos = find_pawn_positions(state)

    board = env.board
    board.player1.pos = tuple(red_pos)
    board.player2.pos = tuple(blue_pos)

    board._horizontal = [[False] * BOARD_SIZE for _ in range(BOARD_SIZE - 1)]
    board._vertical = [[False] * (BOARD_SIZE - 1) for _ in range(BOARD_SIZE)]

    for wall in state['walls']['horizontal']:
        if wall['occupied']:
            board._horizontal[wall['row']][wall['col']] = True

    for wall in state['walls']['vertical']:
        if wall['occupied']:
            board._vertical[BOARD_SIZE - 1 - 1 - wall['row']][wall['col']] = True

    board._sync_wall_tuple_sets()

    # 同步玩家牆數：若有 color 欄位則依 color 分配，否則退回到 name 字串判斷
    for idx, player in enumerate(state.get('players', [])):
        walls_left = int(player.get('wallsLeft', 0))
        color = player.get('color')
        if color == 'red':
            board.player1.walls_left = walls_left
        elif color == 'blue':
            board.player2.walls_left = walls_left
        else:
            # fallback: 第一個玩家對應 player1
            if idx == 0:
                board.player1.walls_left = walls_left
            elif idx == 1:
                board.player2.walls_left = walls_left

    # state['currentPlayer'] 現在應為 'red' 或 'blue'
    if state.get('currentPlayer') == 'red':
        board.current_player = board.player1
        board.other_player = board.player2
    else:
        board.current_player = board.player2
        board.other_player = board.player1

    board.update_all_valid_moves()
    try:
        obs = env._get_observation()
    except OverflowError as e:
        # 印出一些內部狀態以便診斷
        try:
            print('env._get_observation overflow; board state:')
            print(f'p1.pos={board.player1.pos}, p2.pos={board.player2.pos}, p1.walls_left={board.player1.walls_left}, p2.walls_left={board.player2.walls_left}')
        except Exception:
            pass
        raise
    mask = env.action_masks()
    try:
        print(f"board positions: p1={board.player1.pos}, p2={board.player2.pos}; walls_left: p1={board.player1.walls_left}, p2={board.player2.walls_left}; valid_actions={sum(1 for v in mask if v)}")
    except Exception:
        pass
    return obs, mask, state


def click_move(page: Page, x: int, y: int) -> bool:
    page_row = 2 + 2 * (BOARD_SIZE - 1 - y)
    page_col = 2 + 2 * x
    return page.evaluate(
        """
        ([row, col]) => {
            const board = document.querySelector('div[data-tutorial="board"]');
            if (!board) {
                return false;
            }
            for (const el of board.querySelectorAll('div')) {
                const grid = el.style.gridArea;
                if (!grid) {
                    continue;
                }
                const parts = grid.split('/').map((v) => parseInt(v.trim(), 10));
                if (parts.length !== 2) {
                    continue;
                }
                if (parts[0] === row && parts[1] === col) {
                    el.click();
                    return true;
                }
            }
            return false;
        }
        """,
        [page_row, page_col],
    )


def click_wall(page: Page, wall_code: str) -> bool:
    orientation, code = wall_code[0], wall_code[1:]
    column = ord(code[0]) - ord('a')
    groove = int(code[1:])
    page_row = BOARD_SIZE - 1 - groove
    kind = 'horizontal' if orientation == 'h' else 'vertical'
    selector = f'[data-testid="slot-{kind}-{page_row}-{column}"]'
    if page.query_selector(selector) is None:
        return False
    page.click(selector)
    return True


def wait_for_turn_change(page: Page, original_state: Dict, side: str, timeout: int = 60) -> Dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = parse_page_state(page)
        if state is None:
            raise RuntimeError('無法讀取遊戲狀態')
        if state['currentPlayer'] == side:
            return state
        if state != original_state:
            original_state = state
        time.sleep(0.5)
    raise TimeoutError(f'等待 {side} 回合超時')


def run_bot(model_path: str, url: str, side: str, headless: bool):
    env = QuoridorEnv()
    model = MaskablePPO.load(model_path)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        page = browser.new_page()
        page.goto(url, timeout=30000)
        page.wait_for_selector('div[data-tutorial="board"]', timeout=20000)

        last_state = None
        step = 0

        while step < 200:
            obs, mask, state = sync_env_from_page(page, env)
            last_state = state

            current_player = state['currentPlayer']
            print(f'回合 {step + 1}: current_player={current_player}')

            if side != 'both' and current_player != side:
                print(f'等待 {side} 遊玩...')
                state = wait_for_turn_change(page, state, side)
                continue

            if not any(mask):
                raise RuntimeError('此狀態下沒有合法動作')

            action, _ = model.predict(obs, action_masks=mask, deterministic=True)
            action = int(action)
            move_type, param = action_id_to_action(action)
            print(f'選擇動作: {move_type} {param}')

            if move_type == 'move':
                x, y = pos_to_xy(param)
                clicked = click_move(page, x, y)
            else:
                clicked = click_wall(page, param)

            if not clicked:
                raise RuntimeError(f'無法在網頁上點擊動作: {move_type} {param}')

            step += 1
            time.sleep(0.6)

            obs, mask, state = sync_env_from_page(page, env)
            if env.board.check_win() is not None:
                print('遊戲結束')
                break

        print('腳本執行完成')


def main():
    parser = argparse.ArgumentParser(description='在 barricade.gg/local 網頁上執行 BarricadeGG AI')
    parser.add_argument('--model-path', type=str, default=str(ROOT_DIR / 'models' / 'quoridor_ppo_final.zip'), help='AI 模型檔案路徑')
    parser.add_argument('--url', type=str, default='https://barricade.gg/local', help='Barricade 網頁 URL')
    parser.add_argument('--side', choices=['both', 'red', 'blue'], default='both', help='AI 控制的角色')
    parser.add_argument('--headless', action='store_true', help='是否使用無頭模式')
    args = parser.parse_args()

    if not os.path.exists(args.model_path):
        raise FileNotFoundError(f'模型文件不存在: {args.model_path}')

    run_bot(args.model_path, args.url, args.side, args.headless)


if __name__ == '__main__':
    main()
