# Regress Stack

Welcome to **Regress Stack**! Regress Stack is a straightforward Ubuntu OpenStack package configurator. It is designed to simplify the process of setting up an OpenStack environment for testing purposes. With Regress Stack, you can easily configure OpenStack packages on a single node and run basic smoke tests to verify the functionality of the packages.

## Getting Started

To get started with Regress Stack, follow these simple steps:

1. **Clone the Repository**:

   ```bash
   git clone https://github.com/canonical/regress-stack.git
   cd regress-stack
   ```

3. **Install pre-commit**:

   ```bash
   uvx pre-commit install
   ```

2. **Install Dependencies**:

   ```bash
   sudo apt install dpkg-dev python3-dev python-apt-dev
   uv sync
   ```

3. **Run Tests**:

   ```bash
   uv run py.test
   ```

4. **Run the Regress Stack**:

   ```bash
   uv run regress-stack setup
   uv run regress-stack test
   ```

## Contributing

We welcome contributions from the community! If you have ideas for new features or improvements, feel free to open an issue or submit a pull request.

## License

This project is licensed under the GNU General Public License v3.0 only. See the [LICENSE](LICENSE) file for details.

Happy Testing!
