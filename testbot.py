import argparse
import sys
import time
from pathlib import Path
from typing import Dict, Tuple
import re

# 將專案根目錄加入 path，以便導入 barricade_core
ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))

from playwright.sync_api import Page, sync_playwright
from barricade_core import QuoridorEnv

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


def click_move(page: Page, x: int, y: int) -> bool:
    """點擊棋盤上的指定座標以移動棋子。"""
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


def click_wall(page: Page, orientation: str, row: int, col: int) -> bool:
    """點擊棋盤上放置牆體。
    
    Args:
        orientation: 'h' (水平) 或 'v' (垂直)
        row: 行號 (0-8)
        col: 列號 (0-8)
    """
    kind = 'horizontal' if orientation == 'h' else 'vertical'
    selector = f'[data-testid="slot-{kind}-{row}-{col}"]'
    if page.query_selector(selector) is None:
        return False
    page.click(selector)
    return True


def print_status(state: Dict):
    """打印棋盤狀態。"""
    print("\n=== 棋盤狀態 ===")
    print(f"當前玩家: {state['currentPlayer']}")
    print(f"玩家信息:")
    for player in state['players']:
        active = "✓" if player['active'] else " "
        print(f"  [{active}] {player['name']} ({player['color']}) - 牆剩餘: {player['wallsLeft']}")

    print(f"\n棋子位置:")
    for pawn in state['pawns']:
        print(f"  {pawn['color'].upper()}: ({pawn['cx']:.1f}, {pawn['cy']:.1f})")

    try:
        red_pos, blue_pos = find_pawn_positions(state)
        print(f"  RED board: {red_pos}")
        print(f"  BLUE board: {blue_pos}")
    except Exception as e:
        print(f"  無法計算棋盤座標: {e}")

    print(f"\n牆體數量: 水平 {sum(1 for w in state['walls']['horizontal'] if w['occupied'])}, 垂直 {sum(1 for w in state['walls']['vertical'] if w['occupied'])}")


def print_help():
    """打印幫助信息。"""
    print("""
=== TestBot 幫助 ===
指令列表:
  status              - 顯示當前棋盤狀態
  move <x> <y>        - 移動棋子到座標 (x, y)，例如: move 4 5
  wall <h|v> <row> <col> - 放置牆體
                        例如: wall h 0 1 (水平牆在第 0 行第 1 欄)
                             wall v 2 3 (垂直牆在第 2 行第 3 欄)
  help                - 顯示此幫助信息
  quit                - 退出程式
""")


def run_test_bot(url: str, headless: bool):
    """運行測試 bot，支援互動式命令輸入。"""
    env = QuoridorEnv()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        page = browser.new_page()
        page.goto(url, timeout=30000)
        page.wait_for_selector('div[data-tutorial="board"]', timeout=20000)

        print(f"✓ 已連接到 {url}")
        print_help()

        while True:
            try:
                state = parse_page_state(page)
                if state is None:
                    print("✗ 無法讀取棋盤狀態")
                    continue

                user_input = input("\n> ").strip()
                if not user_input:
                    continue

                tokens = user_input.split()
                cmd = tokens[0].lower()

                if cmd == 'quit':
                    print("退出程式...")
                    break

                elif cmd == 'help':
                    print_help()

                elif cmd == 'status':
                    print_status(state)

                elif cmd == 'move':
                    if len(tokens) < 3:
                        print("✗ 用法: move <x> <y>")
                        continue
                    try:
                        x, y = int(tokens[1]), int(tokens[2])
                        if not (0 <= x < BOARD_SIZE and 0 <= y < BOARD_SIZE):
                            print(f"✗ 座標超出範圍 (應為 0-{BOARD_SIZE-1})")
                            continue
                        print(f"執行: 移動棋子到 ({x}, {y})")
                        result = click_move(page, x, y)
                        if result:
                            print("✓ 點擊成功")
                            time.sleep(0.5)
                        else:
                            print("✗ 點擊失敗（可能座標不存在或不可選）")
                    except ValueError:
                        print("✗ 座標必須是整數")

                elif cmd == 'wall':
                    if len(tokens) < 4:
                        print("✗ 用法: wall <h|v> <row> <col>")
                        continue
                    try:
                        orientation = tokens[1].lower()
                        row, col = int(tokens[2]), int(tokens[3])
                        if orientation not in ['h', 'v']:
                            print("✗ 方向必須是 'h' (水平) 或 'v' (垂直)")
                            continue
                        if not (0 <= row < BOARD_SIZE and 0 <= col < BOARD_SIZE):
                            print(f"✗ 座標超出範圍 (應為 0-{BOARD_SIZE-1})")
                            continue
                        wall_type = "水平" if orientation == 'h' else "垂直"
                        print(f"執行: 放置 {wall_type} 牆在 ({row}, {col})")
                        result = click_wall(page, orientation, row, col)
                        if result:
                            print("✓ 放置成功")
                            time.sleep(0.5)
                        else:
                            print("✗ 放置失敗（可能位置無效或已有牆體）")
                    except ValueError:
                        print("✗ 行列必須是整數")

                else:
                    print(f"✗ 未知指令: {cmd}，輸入 'help' 查看幫助")

            except KeyboardInterrupt:
                print("\n已中斷")
                break
            except Exception as e:
                print(f"✗ 發生錯誤: {e}")


def main():
    parser = argparse.ArgumentParser(description='TestBot - 測試 Quoridor 遊戲網頁動作')
    parser.add_argument('--url', type=str, default='https://barricade.gg/local', help='Barricade 網頁 URL')
    parser.add_argument('--headless', action='store_true', help='是否使用無頭模式')
    args = parser.parse_args()

    run_test_bot(args.url, args.headless)


if __name__ == '__main__':
    main()
