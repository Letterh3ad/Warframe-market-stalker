def register(parser):
    parser.add_argument("query")
    parser.add_argument("--limit", type=int, default=20)
    parser.set_defaults(handler=lambda args: 0)
