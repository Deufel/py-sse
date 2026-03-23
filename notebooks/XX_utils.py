import marimo

__generated_with = "0.21.1"
app = marimo.App()


@app.cell
def _():
    import httpx, re

    resp = httpx.get("https://raw.githubusercontent.com/flekschas/simple-world-map/master/world-map.min.svg")
    paths = re.findall(r'<path[^>]*\bd="([^"]+)"', resp.text)
    print(f"Found {len(paths)} paths, total {sum(len(p) for p in paths)} chars")

    # Save as Python module
    lines = [
        '# Simplified world map paths (CC BY-SA 3.0, Al MacDonald / Fritz Lekschas)',
        '# Source: https://github.com/flekschas/simple-world-map',
        'VIEWBOX = (30.767, 241.591, 784.077, 458.627)',
        '',
        'PATHS = [',
    ]
    for p in paths:
        lines.append(f'    "{p}",')
    lines.append(']')

    with open('world_paths.py', 'w') as f:
        f.write('\n'.join(lines))
    print("Saved world_paths.py")
    return


if __name__ == "__main__":
    app.run()
