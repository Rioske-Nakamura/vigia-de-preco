# Vigia de Preço

Painel pessoal que roda no seu computador, em português e em reais. Requer Python 3.10 ou mais recente. Usa só a biblioteca padrão do Python.

## Como iniciar

1. Extraia todo o ZIP em uma pasta.
2. No Windows, execute **Iniciar-Vigia.bat**. No Linux ou macOS, abra um terminal nessa pasta e execute `python3 app.py`.
3. Abra [http://127.0.0.1:8765](http://127.0.0.1:8765) no seu navegador.
4. Abra **Guia de instalação** no painel para configurar as fontes, validar cupons e usar o frete Meli+.

O programa continua consultando com a aba fechada. Para operar dia e noite, mantenha o computador com internet, acordado e com o terminal do programa aberto. Se o computador reiniciar, inicie o servidor de novo.

Use **Buscar preços** para comparar o produto imediatamente e **Colocar em vigia** para acompanhar depois. Em **Configurações**, salve uma chave da SerpApi para habilitar Google Shopping Brasil nas lojas sem fonte própria configurada.

O intervalo inicial é de 60 minutos. Ao cadastrar uma vigia com fonte configurada, o servidor a inclui na próxima verificação de tarefas. O painel permite verificar agora, pausar, retomar e comparar ofertas.

## Dados e configuração

- Busca manual, cadastro de ofertas, comparação, simulação e histórico funcionam sem credenciais.
- Consulta automática usa Google Shopping via SerpApi, Mercado Livre DevCenter, Keepa Brasil ou catálogo KaBuM da Awin, conforme o acesso que você configurar em `config.local.json`. O guia na aplicação detalha os requisitos.
- Confira cupons exclusivos, frete do checkout, Prime, Meli+ e parcelas na sua sessão na loja. Registre os valores que você viu para que entrem no cálculo ou deixem a simulação marcada como tal.
- O painel fica em `127.0.0.1`. Para backup ou migração, pare o servidor e copie a pasta `dados`. Use uma instância por pasta de dados.
- Se a porta estiver ocupada, rode `python3 app.py --port 8766` e abra o endereço exibido. No Windows, o comando equivalente é `py -3 app.py --port 8766`.

## Avisos de cupons

A aba **Cupons** mostra a última consulta de cada fonte. Em **Alertas**, a correspondência de um cupom aparece com os produtos encontrados e seus preços quando a fonte os informa. O servidor compara cada campanha ativa com todos os produtos em vigia e suprime avisos repetidos para as mesmas correspondências.

- **KaBuM via Awin**: obtenha o ID de publisher e o token pessoal da Awin. Adicione as chaves `AWIN_PUBLISHER_ID` e `AWIN_API_TOKEN` no seu `config.local.json` e reinicie. Uma campanha que só informar texto ou link pode gerar uma possível correspondência.
- **Feed autorizado**: em `COUPON_FEEDS`, indique a URL HTTPS de cada loja que disponibiliza um catálogo de cupons nesse formato. Use a chave `meli`, `amazon` ou `kabum`. A busca de preços no Google Shopping e a consulta a feeds de cupons usam fontes independentes.

Exemplo de conteúdo para um feed:

```json
{
  "coupons": [
    {
      "id": "campanha-123",
      "code": "EXEMPLO10",
      "title": "10% nos itens da campanha",
      "terms": "Conforme elegibilidade no checkout",
      "starts": "2026-11-18T12:00:00Z",
      "expires": "2026-11-19T02:00:00Z",
      "minimum": 100,
      "products": [
        {"title": "SSD NVMe 1 TB", "sku": "EXEMPLO-SSD", "price": 550, "coupon_price": 495}
      ]
    }
  ]
}
```

Mantenha o `id` estável durante a campanha e use datas com fuso. Em `products`, inclua `title` e, quando disponíveis, `sku`, `url` do anúncio, `price` e `coupon_price`, em reais. A lista completa deve estar no JSON. O painel só mostra preço com cupom quando a fonte fornece esse valor.
