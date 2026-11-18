# Manual técnico de Atlas — 1. Arquitectura de la plataforma

Atlas es la plataforma interna de seguimiento de envíos de la empresa ficticia Cordillera Logística. Este manual describe su arquitectura, sus entornos y las decisiones técnicas principales. Es el documento de referencia para cualquier persona que se incorpore al equipo de ingeniería.

## 1.1 Visión general

Atlas recibe eventos de los escáneres de los centros de distribución, calcula el estado de cada paquete y expone esa información a tres consumidores: la aplicación móvil de los repartidores, el portal web de clientes y el equipo de atención al cliente. La plataforma procesa en promedio 1,2 millones de eventos por día, con picos de hasta 4 millones durante la campaña de fin de año (del 15 de noviembre al 10 de enero).

La arquitectura sigue un estilo de microservicios orientados a eventos. Cada servicio es dueño de sus datos y se comunica con los demás de dos formas: de manera síncrona mediante APIs HTTP internas y de manera asíncrona mediante el bus de eventos.

## 1.2 Servicios principales

La plataforma está compuesta por cinco servicios:

- **atlas-ingest**: recibe los eventos de los escáneres por HTTP, valida su formato y los publica en el bus de eventos. Está escrito en Go y es el único servicio expuesto a la red de los centros de distribución.
- **atlas-tracking**: consume los eventos, calcula el estado actual de cada paquete (en bodega, en ruta, entregado, devuelto) y lo guarda en la base de datos principal. Está escrito en Python con FastAPI.
- **atlas-routes**: calcula las rutas de reparto de cada día a partir de los paquetes asignados a cada repartidor. Se ejecuta como un proceso por lotes todos los días a las 04:30, hora de Santiago.
- **atlas-notify**: envía las notificaciones push a la aplicación móvil y los mensajes SMS a los clientes cuando un paquete cambia de estado.
- **atlas-gateway**: es la puerta de entrada pública. Autentica las peticiones, aplica límites de tasa y enruta cada petición al servicio interno correspondiente.

## 1.3 Bus de eventos y almacenamiento

El bus de eventos es un clúster de Apache Kafka con tres brokers. Los eventos de escaneo se publican en el tópico `scans.v2`, que tiene 24 particiones y una retención de 7 días. La clave de partición es el identificador del paquete, de modo que todos los eventos de un mismo paquete se procesan en orden.

La base de datos principal es PostgreSQL 16, con una réplica de solo lectura que usan los reportes y el portal de clientes. Los datos de seguimiento de más de 18 meses se archivan en almacenamiento de objetos en formato Parquet y se eliminan de la base de datos principal el primer domingo de cada mes.

Redis se usa únicamente como caché del estado de los paquetes consultados con más frecuencia, con un tiempo de vida (TTL) de 120 segundos. Nunca debe usarse Redis como fuente de verdad: si la caché y PostgreSQL no coinciden, manda PostgreSQL.

## 1.4 Entornos

Atlas tiene tres entornos:

1. **dev**: entorno compartido para pruebas de integración. Se reinicia con datos sintéticos todos los lunes a las 06:00.
2. **staging**: copia de producción con datos anonimizados. Todo cambio debe pasar al menos 24 horas en staging antes de llegar a producción, salvo las correcciones urgentes descritas en el capítulo de despliegue.
3. **prod**: producción. Corre en dos zonas de disponibilidad; cada servicio tiene como mínimo tres réplicas.

Los tres entornos corren sobre Kubernetes. Los manifiestos se gestionan con Helm y viven en el repositorio `atlas-deploy`.

## 1.5 Objetivos de nivel de servicio

El equipo se compromete con los siguientes objetivos de nivel de servicio (SLO):

- Disponibilidad de atlas-gateway: 99,9 % mensual.
- Latencia de la API pública: el percentil 95 debe ser menor a 300 milisegundos.
- Frescura del estado: el 99 % de los eventos de escaneo debe verse reflejado en el estado del paquete en menos de 60 segundos.

Cuando el presupuesto de error mensual de un SLO se consume por completo, se congelan los despliegues de nuevas funcionalidades en el servicio afectado hasta el inicio del mes siguiente; solo se permiten correcciones de errores y mejoras de fiabilidad.

## 1.6 Observabilidad

Todos los servicios exponen métricas en formato Prometheus en la ruta `/metrics` y escriben logs estructurados en JSON. Cada log debe incluir los campos `service`, `trace_id` y `package_id` cuando corresponda. Las trazas distribuidas usan OpenTelemetry y se muestrean al 10 % en producción y al 100 % en staging.

Los paneles oficiales de cada servicio están en Grafana, en la carpeta "Atlas / Servicios". Las alertas se definen como código en el repositorio `atlas-alerts` y cualquier alerta nueva requiere la aprobación de otra persona del equipo.
